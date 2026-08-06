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
                '  "schema": "taskgov-document-authority-v3",',
                '  "schema": "taskgov-document-authority-v3",\n  "schema": "taskgov-document-authority-v3",',
                "authority_registry",
            ),
            (
                '      "path": "docs/execution-contracts/tg-m22-evidence-ledger.md",\n      "route_anchor": "tg-m22-sequence",\n      "current_units": [],',
                '      "path": "docs/execution-contracts/tg-m22-evidence-ledger.md",\n      "route_anchor": "tg-m22-sequence",\n      "current_units": ["TG-M22.4"],',
                "authority_registry",
            ),
            (
                '      "path": "docs/execution-contracts/tg-m23-derived-evidence.md",\n      "route_anchor": "tg-m23-derived-evidence",\n      "current_units": [],',
                '      "path": "docs/execution-contracts/tg-m23-derived-evidence.md",\n      "route_anchor": "tg-m23-derived-evidence",\n      "current_units": ["TG-M23.2"],',
                "authority_registry",
            ),
            (
                '      "inactive_units": ["TG-M23.3"]',
                '      "inactive_units": []',
                "authority_registry",
            ),
            (
                '      "inactive_units": ["TG-M23.3"]',
                '      "inactive_units": ["TG-M23.2", "TG-M23.3"]',
                "authority_registry",
            ),
            (
                '          "owner_scope": "windows_process_private_temp_atomic_publication"',
                '          "owner_scope": "windows_process_and_publication"',
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

        self.assertEqual(
            contract.FIRST_HEADINGS[contract.M23_PROCESS],
            "# TG-M23 Windows Process Safety And Atomic Publication Contract",
        )
        self.assertEqual(contract.BUDGETS[contract.M23_PROCESS], (120, 20_000))
        self.assertIn(
            (
                contract.M23,
                "## Process Safety Route",
                ("tg-m23-process-safety.md#tg-m23-process-safety",),
            ),
            contract.ROUTE_SECTIONS,
        )
        self.assertIn(
            (
                contract.M23_PROCESS,
                "## Parent Route",
                ("tg-m23-derived-evidence.md#tg-m23-1",),
            ),
            contract.ROUTE_SECTIONS,
        )

        with self.fixture() as root:
            root.joinpath(*contract.M23_PROCESS.split("/")).unlink()
            self.assertIn(
                "document_unavailable",
                self.codes(contract.check_document_contract(root)),
            )

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
                "# TG-M22 Evidence Ledger Accepted Execution Contract",
                "# TG-M22 Evidence Ledger Current And Conditional Execution Contract",
                "document_role",
            ),
            (
                contract.M23,
                "# TG-M23 Derived Evidence Current And Conditional Execution Contract",
                "# TG-M23 Derived Evidence Conditional Execution Contract",
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
                "Each indexed sequence file is the sole detailed execution owner/router for its",
                "Each indexed sequence file is only an informal summary for its",
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
                "offline/mock TG-M23.2 are accepted predecessors.",
                "offline/mock TG-M23.2 remains inactive.",
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

    def test_current_schema_v19_viewer_and_evidence_markers_are_closed(self):
        relative = "docs/execution-contracts/tg-m22-evidence-ledger.md"
        mutations = (
            ("v0.12.0 candidate is schema v19, Viewer snapshot v4 with sources v5-v19, 21", "v0.12.0 candidate is schema v18, Viewer snapshot v4 with sources v5-v18, 21"),
            ("`evidence_projection_deferred`", "`projection_deferred`"),
            ("adds `evidence_projection_publish` to its ordered write vocabulary plus an", "adds `viewer_publish` to its ordered write vocabulary plus an"),
            ("Doctor reports only stored Evidence-projection", "Doctor repairs stored Evidence-projection"),
            ("Post-commit order is Evidence projection, Viewer refresh, then due backup;", "Post-commit order is Viewer refresh, Evidence projection, then due backup;"),
        )
        for old, new in mutations:
            with self.subTest(old=old):
                with self.fixture() as root:
                    self.replace(root, relative, old, new)
                    self.assertIn("document_role", self.codes(contract.check_document_contract(root)))

    def test_m23_core_and_process_structural_canaries_are_closed(self):
        cases = (
            (
                contract.M23,
                "this document is the sole TG-M23 unit owner/router",
                "this document shares TG-M23 unit ownership",
            ),
            (
                contract.M23,
                "Stdin exactly=`ASCII(\"taskgov-analysis-stdin-v1\")||LF",
                "Stdin may use implementation-defined framing ",
            ),
            (
                contract.M23,
                "payload order=`report_id,analysis_job_id,source_kind,source_key,recipe_digest,inference_state",
                "payload order=`analysis_job_id,report_id,source_kind,source_key,recipe_digest,inference_state",
            ),
            (
                contract.M23,
                "Markdown v1=`T||LF||LF||join(B1..B10,LF||LF)||LF`",
                "Markdown v1 uses an implementation-defined layout",
            ),
            (
                contract.M23_PROCESS,
                "This document creates no second owner.",
                "This document also owns TG-M23 unit state.",
            ),
            (
                contract.M23_PROCESS,
                "No B thread subsequently creates or receives a window, hook, clipboard",
                "B may receive a desktop-bound IPC channel",
            ),
            (
                contract.M23_PROCESS,
                "`STARTUPINFOEXW` with no `STARTF_USESTDHANDLES` and exactly two attributes",
                "`STARTUPINFOEXW` may inherit ambient handles",
            ),
            (
                contract.M23_PROCESS,
                "B owns exactly one bounded stdin writer and one bounded drain worker for each of stdout and stderr.",
                "B may create unbounded detached I/O workers.",
            ),
            (
                contract.M23_PROCESS,
                "held enumeration proves exact `{report.json,report.md}` and S/O absence",
                "held enumeration permits S/O in the no-adapter root",
            ),
            (
                contract.M23_PROCESS,
                "Root exact DACL=`DENY OW(WC|WO);ALLOW CU(R0.access);ALLOW RC(FT|RA)`",
                "Root DACL may grant RC write access",
            ),
            (
                contract.M23_PROCESS,
                "`DP=(DL|DA|RA|SY,SW,OPEN_EXISTING,K|X)`",
                "`DP=(DL|DA|RA|SY,0,OPEN_EXISTING,K|X)`",
            ),
            (
                contract.M23_PROCESS,
                "`TH=(CR|DELETE|RD|WD|RA|SY,0,CREATE_NEW,X)`",
                "`TH=(DELETE|RD|WD|RA|SY,0,CREATE_NEW,X)`",
            ),
            (
                contract.M23_PROCESS,
                "C retains the same lease continuously without `UnlockFileEx` or close",
                "C releases the lease before retry",
            ),
            (
                contract.M23_PROCESS,
                "`publish_ready` requires valid report/Markdown temps",
                "Publication may begin with an incomplete report temp",
            ),
        )
        for relative, old, new in cases:
            with self.subTest(relative=relative, marker=old[:40]):
                with self.fixture() as root:
                    self.replace(root, relative, old, new)
                    self.assertIn(
                        "document_role",
                        self.codes(contract.check_document_contract(root)),
                    )

        core = ROOT.joinpath(*contract.M23.split("/")).read_text(encoding="utf-8")
        process = ROOT.joinpath(*contract.M23_PROCESS.split("/")).read_text(
            encoding="utf-8"
        )
        self.assertEqual(core.count("sole TG-M23 unit owner/router"), 1)
        self.assertNotIn("sole TG-M23 unit owner/router", process)
        self.assertEqual(
            process.count("creates no second owner"),
            1,
        )

    def test_m23_digest_vectors_are_independent_and_exact(self):
        identity = (
            b'{"bundle_state":"legacy_unknown","completion_cycle_id":"c",'
            b'"cycle_ordinal":1,"project_id":"p","task_id":"t"}'
        )
        recipe = (
            b'{"declared_model_id":null,"inference_mode":"offline",'
            b'"producer_version":1,"prompt_schema_version":1,'
            b'"renderer_version":1,"report_schema_version":1}'
        )
        source_key = "sha256:" + hashlib.sha256(
            b"taskgov-analysis-source-v1\0" + identity
        ).hexdigest()
        recipe_digest = "sha256:" + hashlib.sha256(
            b"taskgov-analysis-recipe-v1\0" + recipe
        ).hexdigest()
        job_hash = hashlib.sha256(
            b"taskgov-analysis-job-v1\0"
            + source_key.encode("ascii")
            + b"\0"
            + recipe_digest.encode("ascii")
        ).hexdigest()
        self.assertEqual(
            source_key,
            "sha256:43de9c707c10c49ab1b3bc939975b058bbf9b79dfbd495324ecd5e2135581fbf",
        )
        self.assertEqual(
            recipe_digest,
            "sha256:8ac0a31a34894d0d759b7844b8f0d8b6999520374f34a73b45a2a4cff7b29f3d",
        )
        self.assertEqual(
            "tg_analysis_job_" + job_hash[:16],
            "tg_analysis_job_ec713ed4ae8e2860",
        )

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

    def test_m23_split_history_is_exact_and_indexed_once(self):
        relative = "v0.12.0/tg-m23-pre-process-safety-split.md"
        capture = ROOT / "docs" / "history" / relative
        raw = capture.read_bytes()
        self.assertEqual(
            hashlib.sha256(raw).hexdigest(),
            "86058642778a135dca524d7f2ae89091ba9740c6883cff9acedabcf0b1f6ba9c",
        )
        source = subprocess.run(
            ["git", "show", "7313483a9fd160f0ec8127b013d9f5533d2d16ab:docs/execution-contracts/tg-m23-derived-evidence.md"],
            cwd=ROOT,
            check=False,
            capture_output=True,
        )
        self.assertEqual(source.returncode, 0)
        self.assertTrue(raw.endswith(source.stdout))
        index = (ROOT / "docs" / "history" / "README.md").read_text(encoding="utf-8")
        self.assertEqual(index.count(f"]({relative})"), 1)
        self.assertEqual(index.count("](../execution-contracts/tg-m23-process-safety.md#tg-m23-process-safety)"), 1)

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

        for relative in (contract.M23, contract.M23_PROCESS):
            with self.subTest(relative=relative), self.fixture() as root:
                path = root.joinpath(*relative.split("/"))
                raw = path.read_bytes()
                max_lines, max_bytes = contract.BUDGETS[relative]
                path.write_bytes(raw[:-1] + b"x" * (max_bytes - len(raw) + 1) + b"\n")
                result = contract.check_document_contract(root)
                metric = next(item for item in result.metrics if item.path == relative)
                self.assertLessEqual(metric.lines, max_lines)
                self.assertEqual(metric.bytes, max_bytes + 1)
                self.assertIn("document_budget", self.codes(result))

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
