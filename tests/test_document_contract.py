from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from tools import document_contract as contract


ROOT = Path(__file__).resolve().parents[1]


class DocumentContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        issues: list[contract.Issue] = []
        scans: dict[str, contract.Scan] = {}
        files = set(contract.CANONICAL_DOCS) | {".ignore"}
        for relative in contract.CANONICAL_DOCS:
            document = contract._read(ROOT, relative, issues)
            assert document is not None
            scans[relative] = contract._scan(relative, document[1], issues)
        for relative, scan in scans.items():
            for link in scan.links:
                resolved = contract._resolve(ROOT, relative, link.target)
                if resolved is not None:
                    files.add(resolved[0])
        files.update(
            path.relative_to(ROOT).as_posix()
            for path in (ROOT / "docs" / "history").rglob("*.md")
        )
        cls.fixture_files = tuple(sorted(files))

    @contextmanager
    def fixture(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative in self.fixture_files:
                source = ROOT.joinpath(*relative.split("/"))
                target = root.joinpath(*relative.split("/"))
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
            yield root

    @staticmethod
    def replace(root: Path, relative: str, old: str, new: str) -> bytes:
        path = root.joinpath(*relative.split("/"))
        original = path.read_bytes()
        text = original.decode("utf-8")
        if text.count(old) != 1:
            raise AssertionError(f"expected one replacement in {relative}: {old!r}")
        path.write_bytes(text.replace(old, new).encode("utf-8"))
        return original

    @staticmethod
    def codes(result: contract.Result) -> set[str]:
        return {issue.code for issue in result.issues}

    def test_current_repository_is_deterministic_read_only_and_cli_compatible(self):
        before = {
            relative: hashlib.sha256(
                ROOT.joinpath(*relative.split("/")).read_bytes()
            ).hexdigest()
            for relative in self.fixture_files
        }
        first = contract.check_document_contract(ROOT)
        second = contract.check_document_contract(ROOT)
        self.assertTrue(first.ok, first.issues)
        self.assertEqual(first, second)
        after = {
            relative: hashlib.sha256(
                ROOT.joinpath(*relative.split("/")).read_bytes()
            ).hexdigest()
            for relative in self.fixture_files
        }
        self.assertEqual(before, after)

        text_run = subprocess.run(
            [sys.executable, "tools/document_contract.py", "--repo", str(ROOT)],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(text_run.returncode, 0, text_run.stdout + text_run.stderr)
        self.assertIn("document contract: PASS", text_run.stdout)
        json_run = subprocess.run(
            [
                sys.executable,
                "tools/document_contract.py",
                "--repo",
                str(ROOT),
                "--json",
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(json_run.returncode, 0, json_run.stdout + json_run.stderr)
        self.assertTrue(json.loads(json_run.stdout)["ok"])

    def test_scanner_ignores_only_fixed_fences_and_inline_code(self):
        sample = (
            "# Visible\n\n"
            "```text\n# Hidden\n[hidden](missing.md)\n"
            "<a id=\"hidden\"></a>\n```\n\n"
            "`<tag> [inline](missing.md)`\n"
            "multiline `<tag>\n[inline](missing.md)` remains inert\n"
            "hidden `\n\n<a id=\"hidden-inline\"></a>\n\ncontent`\n"
        )
        issues: list[contract.Issue] = []
        scan = contract._scan("sample.md", sample, issues)
        self.assertEqual(issues, [])
        self.assertEqual([line for _level, line, _index in scan.headings], ["# Visible"])
        self.assertEqual(scan.links, [])
        self.assertEqual(scan.anchors, {})

    def test_scanner_rejects_ambiguous_route_bearing_forms(self):
        cases = {
            "indented_fence": ("# T\n\n  ```text\n```\n", "syntax_fence"),
            "tilde_fence": ("# T\n\n~~~text\n~~~\n", "syntax_fence"),
            "unclosed_fence": ("# T\n\n```text\nbody\n", "syntax_fence"),
            "setext": ("# T\n\nHeading\n---\n", "syntax_heading"),
            "indented_heading": ("# T\n\n  ## Hidden\n", "syntax_layout"),
            "raw_html": ("# T\n\n<div>hidden</div>\n", "syntax_html"),
            "comment": ("# T\n\n<!-- hidden -->\n", "syntax_html"),
            "image": ("# T\n\n![x](README.md)\n", "syntax_link"),
            "reference": ("# T\n\n[x][id]\n[id]: README.md\n", "syntax_link"),
            "shortcut_reference": ("# T\n\n  [id]: README.md\n[id]\n", "syntax_link"),
            "title": ("# T\n\n[x](README.md \"title\")\n", "syntax_link"),
            "multiline": ("# T\n\n[x]\n(README.md)\n", "syntax_link"),
            "escaped": ("# T\n\n\\[x](README.md)\n", "syntax_link"),
            "escaped_backtick": ("# T\n\n\\`[x](README.md)\\`\n", "syntax_inline_code"),
            "autolink": ("# T\n\n<https://example.invalid>\n", "syntax_html"),
            "unclosed_inline": ("# T\n\n`route\n", "syntax_inline_code"),
        }
        for name, (sample, expected) in cases.items():
            with self.subTest(name=name):
                issues: list[contract.Issue] = []
                contract._scan("sample.md", sample, issues)
                self.assertIn(expected, {issue.code for issue in issues})

    def test_anchors_and_blockquotes_are_closed_forms(self):
        issues: list[contract.Issue] = []
        contract._scan(
            "sample.md",
            "# T\n<a id=\"route\"></a>\n## Missing blanks\n<a id=\"UPPER\"></a>\n",
            issues,
        )
        self.assertIn("syntax_anchor", {issue.code for issue in issues})

        scans = {
            "README.md": contract._scan(
                "README.md", "# T\n\n> unsupported quote\n", []
            )
        }
        quote_issues: list[contract.Issue] = []
        contract._banners(scans, quote_issues)
        self.assertEqual({issue.code for issue in quote_issues}, {"banner_contract"})

    def test_link_resolution_is_relative_exact_case_and_explicit_anchor_only(self):
        self.assertEqual(
            contract._resolve(ROOT, "README.md", "docs/authority.md#document-authority-index"),
            ("docs/authority.md", "document-authority-index"),
        )
        for target in (
            "https://example.invalid/x",
            "LICENSE?query=1",
            "LICENSE%20copy",
            "license",
            "../outside.md",
            "docs\\authority.md",
            "C:/absolute.md",
        ):
            with self.subTest(target=target):
                self.assertIsNone(contract._resolve(ROOT, "README.md", target))
        with mock.patch.object(contract, "_is_link_like", return_value=True):
            self.assertIsNone(contract._safe_file(ROOT, "README.md"))

        with self.fixture() as root:
            self.replace(
                root,
                "docs/execution-contracts/README.md",
                "tg-m24-verification-runner.md#tg-m24-verification-runner",
                "tg-m24-verification-runner.md#sequence-boundary",
            )
            result = contract.check_document_contract(root)
            self.assertIn("link_anchor", self.codes(result))

    def test_registry_and_routes_reject_hidden_duplicate_extra_and_order_drift(self):
        mutations = (
            (
                '  "schema": "taskgov-document-authority-v2",',
                '  "schema": "taskgov-document-authority-v2",\n  "schema": "taskgov-document-authority-v2",',
                "authority_registry",
            ),
            (
                '    "current_units": ["TG-M21.5"],',
                '    "current_units": ["TG-M22.3"],',
                "authority_registry",
            ),
            (
                '  "history_index": "docs/history/README.md"',
                '  "extra": true,\n  "history_index": "docs/history/README.md"',
                "authority_registry",
            ),
            ("```json", "```text", "authority_registry"),
            (
                "- [AGENTS.md](../AGENTS.md)",
                "```text\n- [AGENTS.md](../AGENTS.md)\n```",
                "authority_route",
            ),
            ("`docs/release-install.md`", "`docs/missing.md`", "authority_route"),
        )
        for old, new, expected in mutations:
            with self.subTest(expected=expected, replacement=new[:20]):
                with self.fixture() as root:
                    self.replace(root, contract.AUTHORITY, old, new)
                    result = contract.check_document_contract(root)
                    self.assertIn(expected, self.codes(result))

    def test_role_and_live_state_drift_are_rejected_outside_fences(self):
        mutations = (
            (
                "AGENTS.md",
                "At the start of every Task, read and follow the minimal start set:",
                "At the start of every Task, optionally read the minimal start set:",
                "agents_routing",
            ),
            (
                "docs/specification.md",
                "# task-governance-tool Current Product Specification",
                "# Wrong Owner",
                "document_role",
            ),
            (
                "docs/specification.md",
                "This document specifies supported product behavior.",
                "This document contains background notes.",
                "document_role",
            ),
            (
                "docs/design.md",
                "# task-governance-tool Current Implementation Design",
                "# task-governance-tool Current Implementation Design\n\n## Completed TG-M20 Study Boundary",
                "document_role",
            ),
            (
                "docs/execution-contracts/tg-m22-evidence-ledger.md",
                "# TG-M22 Evidence Ledger Current And Conditional Execution Contract",
                "# TG-M22 Evidence Ledger Conditional Execution Contract",
                "document_role",
            ),
            (
                contract.EXECUTION_INDEX,
                "# Current And Conditional Execution Contract Index",
                "# Conditional Execution Contract Index",
                "document_role",
            ),
            (
                contract.EXECUTION_INDEX,
                "MIXED CURRENT AND CONDITIONAL FORMAL AUTHORITY.",
                "CONDITIONAL FORMAL AUTHORITY — ACCEPTED BUT INACTIVE.",
                "banner_contract",
            ),
            (
                contract.EXECUTION_INDEX,
                "Each indexed file is the sole detailed execution owner for its named units'",
                "Each indexed file is the sole detailed owner for its named inactive units'",
                "document_role",
            ),
            (
                contract.EXECUTION_INDEX,
                "tg-m22-evidence-ledger.md#tg-m22-sequence",
                "tg-m22-evidence-ledger.md#tg-m22-conditional-product",
                "authority_route",
            ),
            (
                "README.md",
                "TG-M21.5 is current. TG-M22.1A and TG-M22.2 are accepted predecessors, and",
                "TG-M21.5 and all later M22 units are inactive, and",
                "document_role",
            ),
            (
                "plan.md",
                "# task-governance-tool Current Decisions And Open Issues",
                "# task-governance-tool Current Decisions And Open Issues\n\ntg_review_receipt_0123456789abcdef",
                "volatile_state",
            ),
            (
                contract.RELEASE_INSTALL,
                "# Release Candidate And Published Install Record",
                "# Wrong Release Owner",
                "document_role",
            ),
        )
        for relative, old, new, expected in mutations:
            with self.subTest(relative=relative, expected=expected):
                with self.fixture() as root:
                    self.replace(root, relative, old, new)
                    self.assertIn(expected, self.codes(contract.check_document_contract(root)))

    def test_history_is_indexed_and_digested_without_parsing_capture_prose(self):
        with self.fixture() as root:
            added = root / "docs" / "history" / "v0.11.0" / "unindexed.md"
            added.write_bytes(b"# Capture\n\n> NON-AUTHORITATIVE HISTORY.\n")
            self.assertIn("history_index", self.codes(contract.check_document_contract(root)))

        with self.fixture() as root:
            path = root / "docs" / "history" / "v0.11.0" / "pre-m22-completed-execution.md"
            path.write_bytes(path.read_bytes() + b"\nchanged\n")
            self.assertIn("history_provenance", self.codes(contract.check_document_contract(root)))

        with self.fixture() as root:
            old_capture = root / "docs" / "history" / "v0.10.0" / "m20-operational-baseline.md"
            old_capture.write_bytes(
                old_capture.read_bytes() + b"\n[historical tombstone](missing.md)\n"
            )
            self.assertTrue(contract.check_document_contract(root).ok)

    def test_static_sequences_reject_row_gate_and_mirror_drift(self):
        mutations = (
            (
                "docs/execution-contracts/tg-m22-evidence-ledger.md",
                "tg_task_88bfe19eb6cffe2e",
                "tg_task_0000000000000000",
                "sequence_contract",
            ),
            (
                "docs/execution-contracts/tg-m23-derived-evidence.md",
                "accepted TG-M23.1",
                "accepted TG-M23.0",
                "sequence_contract",
            ),
            (
                "docs/execution-contracts/tg-m24-verification-runner.md",
                "Require migration, safety, package, focused/full offline checks",
                "Require only focused checks",
                "sequence_contract",
            ),
            (
                "plan.md",
                "Require full offline, package/release consistency",
                "Require full offline consistency",
                "sequence_m24_mirror",
            ),
        )
        for relative, old, new, expected in mutations:
            with self.subTest(relative=relative):
                with self.fixture() as root:
                    self.replace(root, relative, old, new)
                    self.assertIn(expected, self.codes(contract.check_document_contract(root)))

        with self.fixture() as root:
            relative = "docs/execution-contracts/tg-m23-derived-evidence.md"
            path = root.joinpath(*relative.split("/"))
            text = path.read_text(encoding="utf-8")
            row = "| TG-M23.3 / 30 | `tg_task_0ada32d2b4f9759d` | accepted TG-M23.2 |"
            path.write_bytes(text.replace(row, row + "\n\n  " + row).encode("utf-8"))
            self.assertIn("sequence_contract", self.codes(contract.check_document_contract(root)))

    def test_search_and_size_gates_fail_closed(self):
        with self.fixture() as root:
            path = root / ".ignore"
            path.write_bytes(path.read_bytes() + b"/extra/\n")
            self.assertIn("search_policy", self.codes(contract.check_document_contract(root)))

        with self.fixture() as root:
            path = root / "plan.md"
            path.write_bytes(path.read_bytes() + ("padding\n" * 25).encode("utf-8"))
            self.assertIn("document_budget", self.codes(contract.check_document_contract(root)))

        with self.fixture() as root:
            path = root / "AGENTS.md"
            path.write_bytes(path.read_bytes() + ("context\n" * 160).encode("utf-8"))
            codes = self.codes(contract.check_document_contract(root))
            self.assertIn("document_budget", codes)
            self.assertIn("mandatory_read_set", codes)

    def test_checker_and_focused_tests_remain_bounded(self):
        self.assertLessEqual(
            len((ROOT / "tools" / "document_contract.py").read_text(encoding="utf-8").splitlines()),
            650,
        )
        self.assertLessEqual(len(Path(__file__).read_text(encoding="utf-8").splitlines()), 800)
        self.assertLessEqual(
            len((ROOT / "tests" / "test_document_history.py").read_text(encoding="utf-8").splitlines()),
            800,
        )


if __name__ == "__main__":
    unittest.main()
