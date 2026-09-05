from __future__ import annotations

import hashlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager, redirect_stdout
from pathlib import Path
from unittest import mock

from tools import document_contract as contract


ROOT = Path(__file__).resolve().parents[1]

EXPECTED_CANONICAL_DOCS = (
    "AGENTS.md",
    "README.md",
    "docs/authority.md",
    "docs/specification.md",
    "docs/design.md",
    "plan.md",
    "docs/history/README.md",
)
EXPECTED_METRIC_DOCS = EXPECTED_CANONICAL_DOCS + ("docs/release-install.md",)
FIXTURE_LINK_TARGETS = (
    "LICENSE",
    "docs/releases/v0.10.0.md",
    "docs/releases/v0.11.0.md",
    "docs/releases/v0.12.0.md",
    "docs/releases/v0.13.0.md",
    "task-governance-tool/LICENSE",
    "task-governance-tool/SKILL.md",
    "task-governance-tool/references/cli_contracts.md",
    "task-governance-tool/references/task_workflow.md",
    "task-governance-tool/scripts/task_governance_tool/tasks.py",
    "task-governance-tool/scripts/task_governance_tool/verification_runner_service.py",
    "tests/evidence_reader_oracle.py",
    "tests/test_m242_runner_service.py",
    "tests/test_python314_exception_reporting.py",
    "tests/test_task_validation.py",
    "tools/test_lanes.py",
)
class DocumentContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if tuple(contract.CANONICAL_DOCS) != EXPECTED_CANONICAL_DOCS:
            raise AssertionError("canonical documentation inventory drifted")
        if tuple(contract.METRIC_DOCS) != EXPECTED_METRIC_DOCS:
            raise AssertionError("documentation metric inventory drifted")
        files = set(EXPECTED_METRIC_DOCS) | set(FIXTURE_LINK_TARGETS) | {
            ".ignore",
            ".gitignore",
        }
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
    def replace(root: Path, relative: str, old: str, new: str) -> None:
        path = root.joinpath(*relative.split("/"))
        text = path.read_text(encoding="utf-8")
        if text.count(old) != 1:
            raise AssertionError(f"expected one replacement in {relative}: {old!r}")
        path.write_bytes(text.replace(old, new).encode("utf-8"))

    @staticmethod
    def append(root: Path, relative: str, text: str) -> None:
        path = root.joinpath(*relative.split("/"))
        original = path.read_text(encoding="utf-8")
        path.write_bytes((original + text).encode("utf-8"))

    @staticmethod
    def registry(root: Path) -> dict[str, object]:
        text = (root / contract.AUTHORITY).read_text(encoding="utf-8")
        start = text.index("```json\n") + len("```json\n")
        end = text.index("\n```", start)
        return json.loads(text[start:end])

    @staticmethod
    def write_registry(root: Path, registry: dict[str, object]) -> None:
        path = root / contract.AUTHORITY
        text = path.read_text(encoding="utf-8")
        start = text.index("```json\n") + len("```json\n")
        end = text.index("\n```", start)
        payload = json.dumps(registry, ensure_ascii=False, indent=2)
        path.write_bytes((text[:start] + payload + text[end:]).encode("utf-8"))

    @staticmethod
    def add_history_capture(
        root: Path,
        relative: str,
        body: str,
        *,
        index_count: int,
    ) -> None:
        capture = root / "docs" / "history" / relative
        capture.parent.mkdir(parents=True, exist_ok=True)
        capture.write_bytes(body.encode("utf-8"))
        if index_count:
            links = "\n".join(
                f"- [Fixture capture]({relative})" for _ in range(index_count)
            )
            DocumentContractTests.append(
                root,
                contract.HISTORY_INDEX,
                f"\n## Fixture Capture\n\n{links}\n",
            )

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
        payload = json.loads(json_run.stdout)
        self.assertEqual(set(payload), {"issues", "metrics", "ok"})
        self.assertTrue(payload["ok"])
        self.assertEqual(
            {item["path"] for item in payload["metrics"]},
            set(EXPECTED_METRIC_DOCS),
        )
        self.assertTrue(
            all(set(item) == {"bytes", "lines", "path"} for item in payload["metrics"])
        )

    def test_cli_internal_exception_is_sanitized(self):
        secret = "private-internal-exception-sentinel"
        output = io.StringIO()
        with mock.patch.object(
            contract,
            "check_document_contract",
            side_effect=RuntimeError(secret),
        ), redirect_stdout(output):
            return_code = contract.main(["--repo", str(ROOT), "--json"])

        serialized = output.getvalue()
        payload = json.loads(serialized)
        self.assertEqual(return_code, 1)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["issues"][0]["code"], "checker_internal_error")
        self.assertNotIn(secret, serialized)
        self.assertNotIn("Traceback", serialized)

    def test_registry_v6_is_closed(self):
        expected = {
            "schema": "taskgov-document-authority-v6",
            "mandatory_start": [
                "AGENTS.md",
                "docs/authority.md",
                "live_task_contract",
            ],
            "current": ["docs/specification.md", "docs/design.md", "plan.md"],
            "mixed_execution": [],
            "conditional": [],
            "history_index": "docs/history/README.md",
        }
        with self.fixture() as root:
            registry = self.registry(root)
            self.assertEqual(registry, expected)
            self.write_registry(root, dict(reversed(tuple(registry.items()))))
            self.assertTrue(contract.check_document_contract(root).ok)

        mutations = (
            ("old_schema", lambda value: value.__setitem__("schema", "taskgov-document-authority-v5")),
            (
                "execution_route",
                lambda value: value["mixed_execution"].append(
                    {"path": "docs/extra-owner.md"}
                ),
            ),
            (
                "conditional_route",
                lambda value: value["conditional"].append("docs/conditional.md"),
            ),
            ("missing_owner", lambda value: value["current"].pop()),
            ("unknown_key", lambda value: value.__setitem__("unknown", [])),
        )
        for name, mutate in mutations:
            with self.subTest(registry_mutation=name), self.fixture() as root:
                registry = self.registry(root)
                mutate(registry)
                self.write_registry(root, registry)
                self.assertIn(
                    "authority_registry",
                    self.codes(contract.check_document_contract(root)),
                )

    def test_active_route_sections_links_and_anchors_fail_closed(self):
        self.assertEqual(
            contract.ROUTE_SECTIONS,
            (
                (contract.AUTHORITY, "## Mandatory Start Set", ("../AGENTS.md",)),
                (
                    contract.AUTHORITY,
                    "## Selective Current Authority",
                    ("specification.md", "design.md", "../plan.md"),
                ),
                (
                    contract.AUTHORITY,
                    "## Non-Authoritative History",
                    ("history/README.md",),
                ),
            ),
        )
        with self.fixture() as root:
            self.replace(
                root,
                contract.AUTHORITY,
                "- [AGENTS.md](../AGENTS.md)",
                "- mandatory AGENTS route omitted",
            )
            self.assertIn(
                "authority_route", self.codes(contract.check_document_contract(root))
            )

        inert_routes = (
            ("escaped_link", r"- \[AGENTS.md](../AGENTS.md)"),
            ("indented_code", "    - [AGENTS.md](../AGENTS.md)"),
            ("blockquote", "> - [AGENTS.md](../AGENTS.md)"),
            (
                "html_type_1",
                "<pre>\n- [AGENTS.md](../AGENTS.md)\n</pre>",
            ),
            ("html_comment", "<!-- - [AGENTS.md](../AGENTS.md) -->"),
            (
                "html_processing_instruction",
                "<?route\n- [AGENTS.md](../AGENTS.md)\n?>",
            ),
            (
                "html_declaration",
                "<!DOCTYPE\n- [AGENTS.md](../AGENTS.md)\n>",
            ),
            (
                "html_cdata",
                "<![CDATA[\n- [AGENTS.md](../AGENTS.md)\n]]>",
            ),
            ("html_type_6", "<div>\n- [AGENTS.md](../AGENTS.md)\n\n"),
            (
                "html_type_7",
                '<span title="x>y">\n- [AGENTS.md](../AGENTS.md)\n\n',
            ),
            (
                "list_fence",
                "- ~~~markdown\n  - [AGENTS.md](../AGENTS.md)\n  ~~~",
            ),
            (
                "top_fence_quote_content",
                "~~~markdown\n> ~~~\n- [AGENTS.md](../AGENTS.md)\n~~~",
            ),
        )
        for name, replacement in inert_routes:
            with self.subTest(inert_route=name), self.fixture() as root:
                self.replace(
                    root,
                    contract.AUTHORITY,
                    "- [AGENTS.md](../AGENTS.md)",
                    replacement,
                )
                codes = self.codes(contract.check_document_contract(root))
                self.assertIn("authority_route", codes)
                self.assertNotIn("markdown_structure", codes)

        with self.fixture() as root:
            self.replace(
                root,
                contract.AUTHORITY,
                "- [AGENTS.md](../AGENTS.md)",
                "> ~~~markdown\n~~~\n> ~~~\n"
                "- [AGENTS.md](../AGENTS.md)\n~~~",
            )
            codes = self.codes(contract.check_document_contract(root))
            self.assertIn("authority_route", codes)
            self.assertIn("markdown_structure", codes)

        with self.fixture() as root:
            self.replace(
                root,
                contract.AUTHORITY,
                "- [AGENTS.md](../AGENTS.md)",
                "- [AGENTS.md][agents]\n\n[agents]: ../AGENTS.md",
            )
            self.assertIn(
                "authority_route", self.codes(contract.check_document_contract(root))
            )

        with self.fixture() as root:
            self.append(
                root,
                "README.md",
                "\n[Missing anchor](docs/authority.md#missing-anchor)\n",
            )
            self.assertIn(
                "link_anchor", self.codes(contract.check_document_contract(root))
            )

        with self.fixture() as root:
            self.append(root, "README.md", "\n[Unsafe](C:/absolute.md)\n")
            self.assertIn(
                "link_target", self.codes(contract.check_document_contract(root))
            )

        with self.fixture() as root:
            self.append(
                root,
                contract.AUTHORITY,
                '\n<a id="document-authority-index"></a>\n',
            )
            self.assertIn(
                "anchor_duplicate", self.codes(contract.check_document_contract(root))
            )

        with self.fixture() as root:
            root.joinpath(*contract.DESIGN.split("/")).unlink()
            self.assertIn(
                "document_unavailable",
                self.codes(contract.check_document_contract(root)),
            )

    def test_local_link_resolution_is_exact_and_regular_file_only(self):
        self.assertEqual(
            contract._resolve(
                ROOT,
                "README.md",
                "docs/authority.md#document-authority-index",
            ),
            ("docs/authority.md", "document-authority-index"),
        )
        for target in (
            "license",
            "../outside.md",
            "docs\\authority.md",
            "C:/absolute.md",
            "LICENSE?query=1",
        ):
            with self.subTest(target=target):
                self.assertIsNone(contract._resolve(ROOT, "README.md", target))

        with mock.patch.object(contract, "_is_link_like", return_value=True):
            self.assertIsNone(contract._safe_file(ROOT, "README.md"))

        missing_links = (
            "\n[Missing](docs/does-not-exist.md)\n",
            "\n[Missing][target]\n\n[target]: docs/does-not-exist.md\n",
        )
        for link in missing_links:
            with self.subTest(link=link.strip()), self.fixture() as root:
                self.append(root, "README.md", link)
                self.assertIn(
                    "link_target", self.codes(contract.check_document_contract(root))
                )

    def test_start_reread_and_active_trigger_routes_are_structural(self):
        for relative, heading in (
            ("AGENTS.md", "## Source Of Truth"),
            ("AGENTS.md", "## Reread Rule"),
            (contract.AUTHORITY, "## Trigger Routing"),
        ):
            with self.subTest(section=heading), self.fixture() as root:
                self.replace(root, relative, heading, heading + " Removed")
                self.assertIn(
                    "authority_route",
                    self.codes(contract.check_document_contract(root)),
                )

        with self.fixture() as root:
            path = root / contract.AUTHORITY
            lines = path.read_text(encoding="utf-8").splitlines()
            start = lines.index("## Trigger Routing")
            table_rows = [
                index
                for index in range(start + 1, len(lines))
                if lines[index].startswith("|")
            ]
            self.assertGreaterEqual(len(table_rows), 3)
            del lines[table_rows[-1]]
            path.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
            self.assertIn(
                "authority_route", self.codes(contract.check_document_contract(root))
            )

        with self.fixture() as root:
            path = root / contract.AUTHORITY
            lines = path.read_text(encoding="utf-8").splitlines()
            start = lines.index("## Trigger Routing")
            table_rows = [
                index
                for index in range(start + 1, len(lines))
                if lines[index].startswith("|")
            ]
            lines[table_rows[2]], lines[table_rows[3]] = (
                lines[table_rows[3]],
                lines[table_rows[2]],
            )
            path.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
            self.assertIn(
                "authority_route", self.codes(contract.check_document_contract(root))
            )

        with self.fixture() as root:
            path = root / contract.AUTHORITY
            lines = path.read_text(encoding="utf-8").splitlines()
            start = lines.index("## Trigger Routing")
            table_rows = [
                index
                for index in range(start + 1, len(lines))
                if lines[index].startswith("|")
            ]
            route = contract._cells(lines[table_rows[2]])[1]
            lines[table_rows[2]] = f"| Arbitrary nonempty trigger prose | {route} |"
            path.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
            self.assertTrue(contract.check_document_contract(root).ok)

    def test_active_owner_roles_and_live_state_exclusion_are_structural(self):
        for relative in EXPECTED_METRIC_DOCS:
            with self.subTest(owner=relative), self.fixture() as root:
                path = root.joinpath(*relative.split("/"))
                lines = path.read_text(encoding="utf-8").splitlines()
                lines[0] = "# Wrong Document Role"
                path.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
                self.assertIn(
                    "document_role",
                    self.codes(contract.check_document_contract(root)),
                )

        live_fragments = (
            "\nstatus: in_progress\n",
            "\nreview_target_generation: 7\n",
            "\ntg_verification_receipt_0123456789abcdef\n",
            "\n| status | done |\n",
        )
        for fragment in live_fragments:
            with self.subTest(fragment=fragment.strip()), self.fixture() as root:
                self.append(root, "plan.md", fragment)
                self.assertIn(
                    "volatile_state",
                    self.codes(contract.check_document_contract(root)),
                )

        with self.fixture() as root:
            self.append(root, "plan.md", "\nThe current task is TG-M99.1.\n")
            self.assertTrue(contract.check_document_contract(root).ok)

        with self.fixture() as root:
            self.append(
                root,
                "README.md",
                "\n```text\nTG-M99.1 status: in_progress\n```\n",
            )
            self.assertTrue(contract.check_document_contract(root).ok)

    def test_history_first_declaration_and_exactly_once_index_are_structural(self):
        valid_capture = (
            "# Fixture Capture\n\n"
            "> [!CAUTION]\n"
            "> NON-AUTHORITATIVE HISTORY. Fixture lineage only.\n\n"
            "Captured body.\n"
        )
        with self.fixture() as root:
            self.add_history_capture(
                root,
                "v0.13.0/marker-fixture.md",
                valid_capture,
                index_count=1,
            )
            self.assertTrue(contract.check_document_contract(root).ok)

        structural_forms = (
            (
                "publication-form.md",
                "# NON-AUTHORITATIVE HISTORY — Fixture Capture\n\n"
                "> [!CAUTION]\n> Fixture lineage only.\n",
            ),
            (
                "study-form.md",
                "# Fixture Study\n\n"
                "> **NON-AUTHORITATIVE STUDY HISTORY**\n"
                "> Fixture lineage only.\n",
            ),
            (
                "fence-reentry-form.md",
                "# Fixture Capture\n\n"
                "> [!CAUTION]\n> ~~~text\n> inert\n> ~~~\n"
                "> **NON-AUTHORITATIVE HISTORY**\n",
            ),
            (
                "html-reentry-form.md",
                "# Fixture Capture\n\n"
                "> [!CAUTION]\n> <div>\n> inert\n>\n"
                "> **NON-AUTHORITATIVE HISTORY**\n",
            ),
        )
        for name, body in structural_forms:
            with self.subTest(structural_form=name), self.fixture() as root:
                self.add_history_capture(
                    root,
                    f"v0.13.0/{name}",
                    body,
                    index_count=1,
                )
                self.assertTrue(contract.check_document_contract(root).ok)

        invalid_bodies = (
            ("ordinary", "# Capture\n\nOrdinary prose.\n"),
            ("inline-code", "# Capture\n\n`NON-AUTHORITATIVE HISTORY`\n"),
            (
                "negated",
                "# Capture\n\n> This is not non-authoritative history.\n",
            ),
            (
                "lowercase",
                "# Capture\n\n> non-authoritative history. Lowercase is not the marker.\n",
            ),
            (
                "quoted-fence",
                "# Capture\n\n> ```text\n> NON-AUTHORITATIVE HISTORY\n> ```\n",
            ),
            (
                "quoted-comment",
                "# Capture\n\n> [!CAUTION]\n"
                "> <!-- NON-AUTHORITATIVE HISTORY -->\n",
            ),
            (
                "quoted-indented-code",
                "# Capture\n\n> [!CAUTION]\n"
                ">     NON-AUTHORITATIVE HISTORY\n",
            ),
            (
                "quoted-html-type-1",
                "# Capture\n\n> [!CAUTION]\n> <pre>\n"
                "> NON-AUTHORITATIVE HISTORY\n> </pre>\n",
            ),
            (
                "quoted-html-type-6",
                "# Capture\n\n> [!CAUTION]\n> <div>\n"
                "> NON-AUTHORITATIVE HISTORY\n>\n",
            ),
            (
                "quoted-html-type-7",
                "# Capture\n\n> [!CAUTION]\n> <span title=\"x>y\">\n"
                "> NON-AUTHORITATIVE HISTORY\n>\n",
            ),
            (
                "quoted-list-fence",
                "# Capture\n\n> [!CAUTION]\n> - ~~~text\n"
                ">   NON-AUTHORITATIVE HISTORY\n>   ~~~\n",
            ),
            (
                "quoted-list-fence-reentry",
                "# Capture\n\n> [!CAUTION]\n> - ~~~text\n> ~~~\n"
                ">   ~~~\n> NON-AUTHORITATIVE HISTORY\n> ~~~\n",
            ),
        )
        for name, body in invalid_bodies:
            with self.subTest(history_body=name), self.fixture() as root:
                self.add_history_capture(
                    root,
                    f"v0.13.0/invalid-{name}.md",
                    body,
                    index_count=1,
                )
                self.assertIn(
                    "history_banner", self.codes(contract.check_document_contract(root))
                )

        for index_count in (0, 2):
            with self.subTest(index_count=index_count), self.fixture() as root:
                self.add_history_capture(
                    root,
                    "v0.13.0/index-count.md",
                    valid_capture,
                    index_count=index_count,
                )
                self.assertIn(
                    "history_index", self.codes(contract.check_document_contract(root))
                )

        with self.fixture() as root:
            path = root / contract.HISTORY_INDEX
            lines = path.read_text(encoding="utf-8").splitlines()
            quote = next(index for index, line in enumerate(lines) if line.startswith(">"))
            lines.insert(quote, "Ordinary prose before the warning.")
            path.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
            self.assertIn(
                "history_banner", self.codes(contract.check_document_contract(root))
            )

        with self.fixture() as root:
            unexpected = root / "docs" / "history" / "v0.13.0" / "capture.txt"
            unexpected.parent.mkdir(parents=True, exist_ok=True)
            unexpected.write_bytes(b"not an indexed Markdown capture\n")
            self.assertIn(
                "history_file", self.codes(contract.check_document_contract(root))
            )

    def test_history_search_exclusion_is_effective(self):
        with self.fixture() as root:
            self.append(root, ".ignore", "# Unrelated local rule\n/extra/\n")
            self.assertTrue(contract.check_document_contract(root).ok)

        mutations = (
            ("/docs/history/\n", "/docs/other/\n"),
            ("/docs/history/\n", " /docs/history/\n"),
            ("/docs/history/\n", "/docs\\history/\n"),
        )
        for old, new in mutations:
            with self.subTest(ignore_rule=new.strip()), self.fixture() as root:
                self.replace(root, ".ignore", old, new)
                self.assertIn(
                    "search_policy", self.codes(contract.check_document_contract(root))
                )

        with self.fixture() as root:
            self.append(root, ".ignore", "!/docs/history/\n")
            self.assertIn(
                "search_policy", self.codes(contract.check_document_contract(root))
            )

        for suffix, should_pass in (
            ("!/**\n", False),
            ("!/**\n/docs/history/\n", True),
            ("!/docs/history/\n/docs/history/\n", True),
        ):
            with self.subTest(ordered_rules=suffix), self.fixture() as root:
                self.append(root, ".ignore", suffix)
                result = contract.check_document_contract(root)
                if should_pass:
                    self.assertTrue(result.ok, result.issues)
                else:
                    self.assertIn("search_policy", self.codes(result))

    def test_required_documents_reject_invalid_encoding_and_framing(self):
        mutations = (
            ("invalid_utf8", lambda raw: b"\xff" + raw),
            ("utf8_bom", lambda raw: b"\xef\xbb\xbf" + raw),
            ("missing_final_lf", lambda raw: raw.rstrip(b"\n")),
        )
        for relative in EXPECTED_METRIC_DOCS:
            for name, mutate in mutations:
                with self.subTest(document=relative, encoding_case=name), self.fixture() as root:
                    path = root.joinpath(*relative.split("/"))
                    path.write_bytes(mutate(path.read_bytes()))
                    self.assertIn(
                        "document_encoding",
                        self.codes(contract.check_document_contract(root)),
                    )

        with self.fixture() as root:
            self.append(
                root,
                "README.md",
                "\n> ~~~text\nframing escaped its quote container\n",
            )
            self.assertIn(
                "markdown_structure",
                self.codes(contract.check_document_contract(root)),
            )

        with self.fixture() as root:
            path = root / "README.md"
            lines = path.read_text(encoding="utf-8").splitlines()
            lines[0] = "Task Governance Tool"
            lines.append("> # Task Governance Tool")
            path.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
            self.assertIn(
                "document_role", self.codes(contract.check_document_contract(root))
            )


if __name__ == "__main__":
    unittest.main()
