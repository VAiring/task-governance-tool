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
    "docs/execution-contracts/README.md",
    "docs/execution-contracts/tg-m22-evidence-ledger.md",
    "docs/execution-contracts/tg-m23-derived-evidence.md",
    "docs/execution-contracts/tg-m23-process-safety.md",
    "docs/execution-contracts/tg-m24-verification-runner.md",
    "docs/history/README.md",
)
FIXTURE_LINK_TARGETS = (
    "LICENSE",
    "docs/releases/v0.10.0.md",
    "docs/releases/v0.11.0.md",
    "docs/releases/v0.12.0.md",
    "task-governance-tool/LICENSE",
)
DOC2_ROW = (
    "| TG-DOC.2 / 40 | `tg_task_bf2aa245019f5c9f` | "
    "`TG-M23-DERIVED-EVIDENCE` | accepted TG-M23.3 | "
    "accepted predecessor; required before TG-M24.1 |"
)
DOC3_ROW = (
    "| TG-DOC.3 / 20 | `tg_task_99371b8db2d43eb2` | "
    "`TG-DOC-LIFECYCLE` | accepted TG-M24.4 and accepted TG-DOC.2 | "
    "inactive post-M24 |"
)


class DocumentContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if tuple(contract.CANONICAL_DOCS) != EXPECTED_CANONICAL_DOCS:
            raise AssertionError("canonical documentation inventory drifted")
        files = set(EXPECTED_CANONICAL_DOCS) | set(FIXTURE_LINK_TARGETS) | {
            ".ignore",
            ".gitignore",
            "docs/release-install.md",
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
        self.assertNotIn("mandatory_read_set", payload)
        self.assertTrue(payload["metrics"])
        self.assertEqual(
            set(payload["metrics"][0]),
            {"bytes", "lines", "path"},
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

    def test_ordinary_markdown_and_line_reflow_leave_metrics_advisory(self):
        with self.fixture() as root:
            self.replace(
                root,
                "README.md",
                "closed semantic authority registry, route-scoped syntax, owner/anchor/link\n"
                "reachability,",
                "closed semantic authority registry, route-scoped syntax, "
                "owner/anchor/link reachability,",
            )
            self.append(
                root,
                "README.md",
                "\n## Ordinary Markdown Fixture\n\n"
                "> A normal quotation is presentation, not authority.\n\n"
                "A Setext-style label\n---\n\n"
                "![Local image syntax](README.md)\n\n"
                "[reference-link]: README.md\n"
                "A [reference link][reference-link] and <span>raw HTML</span>.\n\n"
                "[Jump](#ordinary-markdown-fixture)\n"
                + ("\n" * 200),
            )
            result = contract.check_document_contract(root)
            self.assertTrue(result.ok, result.issues)
            metric = next(item for item in result.metrics if item.path == "README.md")
            raw = (root / "README.md").read_bytes()
            self.assertEqual(metric.bytes, len(raw))
            self.assertEqual(metric.lines, len(raw.decode("utf-8").splitlines()))

    def test_route_sections_and_anchors_fail_closed_structurally(self):
        inferred_quote = contract._fence_opener_with_container("  > ~~~text")
        self.assertIsNotNone(inferred_quote)
        assert inferred_quote is not None
        self.assertFalse(
            contract._fence_closes_in_container(
                "> ~~~", inferred_quote[0], inferred_quote[2]
            )
        )

        with self.fixture() as root:
            self.append(root, contract.M24, "\n> ~~~text\nstatus: in_progress\n")
            self.assertIn(
                "markdown_structure",
                self.codes(contract.check_document_contract(root)),
            )

        inert_routes = (
            (
                "html_comment",
                "<!-- - [AGENTS.md](../AGENTS.md) -->",
            ),
            (
                "indented_code",
                "    - [AGENTS.md](../AGENTS.md)",
            ),
            (
                "escaped_link",
                r"- \[AGENTS.md](../AGENTS.md)",
            ),
            (
                "blockquote",
                "> - [AGENTS.md](../AGENTS.md)",
            ),
            (
                "raw_html_block",
                "<pre>\n- [AGENTS.md](../AGENTS.md)\n</pre>",
            ),
            (
                "commonmark_html_block",
                "<p>\n- [AGENTS.md](../AGENTS.md)\n</p>",
            ),
            (
                "html_close_before_blank",
                "<div>\n</div>\n- [AGENTS.md](../AGENTS.md)\n",
            ),
            (
                "html_void_before_blank",
                "<hr>\n- [AGENTS.md](../AGENTS.md)\n",
            ),
            (
                "html_closing_tag_start",
                "</div>\n- [AGENTS.md](../AGENTS.md)\n",
            ),
            (
                "html_processing_instruction",
                "<?route\n- [AGENTS.md](../AGENTS.md)\n?>",
            ),
            (
                "html_cdata",
                "<![CDATA[\n- [AGENTS.md](../AGENTS.md)\n]]>",
            ),
            (
                "html_attribute",
                '<div data-route="- [AGENTS.md](../AGENTS.md)"></div>',
            ),
            (
                "html_quoted_gt_attribute",
                '<span title="x>y">\n- [AGENTS.md](../AGENTS.md)\n',
            ),
            (
                "html_list_container",
                "- <div>\n  [AGENTS.md](../AGENTS.md)\n",
            ),
            (
                "list_tilde_fence",
                "- ~~~markdown\n  - [AGENTS.md](../AGENTS.md)\n  ~~~",
            ),
            (
                "top_fence_quote_content",
                "~~~markdown\n> ~~~\n- [AGENTS.md](../AGENTS.md)\n~~~",
            ),
            (
                "top_fence_list_content",
                "~~~markdown\n- ~~~\n- [AGENTS.md](../AGENTS.md)\n~~~",
            ),
            (
                "quote_fence_top_content",
                "> ~~~markdown\n~~~\n> ~~~\n"
                "- [AGENTS.md](../AGENTS.md)\n~~~",
            ),
            (
                "list_fence_top_content",
                "- ~~~markdown\n~~~\n  ~~~\n"
                "- [AGENTS.md](../AGENTS.md)\n~~~",
            ),
            (
                "list_continuation_fence",
                "- item\n  ~~~markdown\n~~~\n"
                "  - [AGENTS.md](../AGENTS.md)\n  ~~~",
            ),
            (
                "tab_list_fence",
                "-\t~~~markdown\n  ~~~\n"
                "  - [AGENTS.md](../AGENTS.md)\n  ~~~",
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
                self.assertIn(
                    "authority_route",
                    self.codes(contract.check_document_contract(root)),
                )

        with self.fixture() as root:
            self.replace(
                root,
                contract.AUTHORITY,
                "- [AGENTS.md](../AGENTS.md)",
                "- [AGENTS.md][agents]\n\n[agents]: ../AGENTS.md",
            )
            self.assertIn(
                "authority_route",
                self.codes(contract.check_document_contract(root)),
            )

        gateway_mutations = (
            (
                "missing",
                "- [TG-DOC.2](../../plan.md#tg-doc-2) is the accepted "
                "post-M23 predecessor that",
                "- TG-DOC.2 route omitted; accepted post-M23 predecessor",
            ),
            (
                "duplicate",
                "- [TG-DOC.2](../../plan.md#tg-doc-2) is the accepted "
                "post-M23 predecessor that",
                "- [TG-DOC.2](../../plan.md#tg-doc-2) is the accepted "
                "post-M23 predecessor that\n"
                "- [Duplicate TG-DOC.2](../../plan.md#tg-doc-2)",
            ),
            (
                "wrong_anchor",
                "- [TG-DOC.3](../../plan.md#tg-doc-3) preserves the post-M24 "
                "normalization scope",
                "- [TG-DOC.3](../../plan.md#tg-doc-2) preserves the post-M24 "
                "normalization scope",
            ),
        )
        for name, old, new in gateway_mutations:
            with self.subTest(gateway=name), self.fixture() as root:
                self.replace(root, contract.EXECUTION_INDEX, old, new)
                self.assertIn(
                    "authority_route",
                    self.codes(contract.check_document_contract(root)),
                )

        with self.fixture() as root:
            self.replace(
                root,
                contract.AUTHORITY,
                "- [AGENTS.md](../AGENTS.md)",
                "- mandatory AGENTS owner omitted",
            )
            self.assertIn(
                "authority_route",
                self.codes(contract.check_document_contract(root)),
            )

        with self.fixture() as root:
            self.replace(
                root,
                contract.AUTHORITY,
                "| Supported product behavior, public CLI/JSON, persistence, "
                "privacy, setup, Viewer, or current gate | Exact section in "
                "`docs/specification.md` |",
                "| Supported product behavior, public CLI/JSON, persistence, "
                "privacy, setup, Viewer, or current gate | "
                "`docs/specification.md` as a reference |",
            )
            self.assertIn(
                "authority_route",
                self.codes(contract.check_document_contract(root)),
            )

        with self.fixture() as root:
            self.replace(
                root,
                contract.EXECUTION_INDEX,
                "tg-m24-verification-runner.md#tg-m24-verification-runner",
                "tg-m24-verification-runner.md#missing-route-anchor",
            )
            self.assertIn(
                "link_anchor",
                self.codes(contract.check_document_contract(root)),
            )

        with self.fixture() as root:
            self.append(
                root,
                contract.M23_PROCESS,
                '\n<a id="tg-m23-process-safety"></a>\n',
            )
            self.assertIn(
                "anchor_duplicate",
                self.codes(contract.check_document_contract(root)),
            )

    def test_link_resolution_is_exact_case_local_and_regular_file_only(self):
        self.assertEqual(
            contract._resolve(
                ROOT,
                "README.md",
                "docs/authority.md#document-authority-index",
            ),
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
            root.joinpath(*contract.M23_PROCESS.split("/")).unlink()
            self.assertIn(
                "document_unavailable",
                self.codes(contract.check_document_contract(root)),
            )

        with self.fixture() as root:
            self.append(root, "README.md", "\n[Machine path](C:/absolute.md)\n")
            self.assertIn(
                "link_target",
                self.codes(contract.check_document_contract(root)),
            )

        with self.fixture() as root:
            self.append(
                root,
                "README.md",
                '\n[Broken titled](docs/does-not-exist.md "title")\n',
            )
            self.assertIn(
                "link_target",
                self.codes(contract.check_document_contract(root)),
            )

        with self.fixture() as root:
            self.append(
                root,
                "README.md",
                '\n[Broken reference][missing]\n\n'
                '[missing]: docs/does-not-exist.md "title"\n',
            )
            self.assertIn(
                "link_target",
                self.codes(contract.check_document_contract(root)),
            )

        with self.fixture() as root:
            self.append(
                root,
                "README.md",
                '\n[Authority](docs/authority.md "owner")\n'
                '[Authority reference][authority]\n\n'
                '[authority]: docs/authority.md "owner"\n',
            )
            result = contract.check_document_contract(root)
            self.assertTrue(result.ok, result.issues)

    def test_bounded_start_reread_and_trigger_routes_are_structural(self):
        for heading in ("## Source Of Truth", "## Reread Rule"):
            with self.subTest(missing_section=heading), self.fixture() as root:
                self.replace(root, "AGENTS.md", heading, heading + " Removed")
                self.assertIn(
                    "authority_route",
                    self.codes(contract.check_document_contract(root)),
                )

        with self.fixture() as root:
            self.replace(
                root,
                "AGENTS.md",
                "- Re-read the minimal start set at the start of each new Task",
                "- Never re-read the minimal start set at the start of each new Task",
            )
            self.assertIn(
                "authority_route",
                self.codes(contract.check_document_contract(root)),
            )

        owner_negations = (
            "- Do not ever follow `AGENTS.md` for agent behavior, safety, and "
            "workflow discipline.",
            "- `AGENTS.md` does not own agent behavior, safety, or workflow "
            "discipline.",
        )
        for replacement in owner_negations:
            with self.subTest(owner_negation=replacement), self.fixture() as root:
                self.replace(
                    root,
                    "AGENTS.md",
                    "- Follow `AGENTS.md` for agent behavior, safety, and workflow "
                    "discipline.",
                    replacement,
                )
                self.assertIn(
                    "authority_route",
                    self.codes(contract.check_document_contract(root)),
                )

        with self.fixture() as root:
            self.replace(
                root,
                "AGENTS.md",
                "- Follow `AGENTS.md` for agent behavior, safety, and workflow "
                "discipline.",
                "- Do not follow `AGENTS.md` for agent behavior, safety, and "
                "workflow discipline.",
            )
            self.assertIn(
                "authority_route",
                self.codes(contract.check_document_contract(root)),
            )

        with self.fixture() as root:
            self.replace(
                root,
                "AGENTS.md",
                "- Follow `AGENTS.md` for agent behavior, safety, and workflow "
                "discipline.",
                "- Do not rely on memory, and follow `AGENTS.md` for agent "
                "behavior, safety, and workflow discipline.",
            )
            result = contract.check_document_contract(root)
            self.assertTrue(result.ok, result.issues)

        with self.fixture() as root:
            self.replace(
                root,
                "AGENTS.md",
                "- Follow `AGENTS.md` for agent behavior, safety, and workflow "
                "discipline.",
                "- Do not rely on memory, and do not follow `AGENTS.md` for "
                "agent behavior, safety, and workflow discipline.",
            )
            self.assertIn(
                "authority_route",
                self.codes(contract.check_document_contract(root)),
            )

        with self.fixture() as root:
            self.replace(
                root,
                "AGENTS.md",
                "- For product behavior, prefer `docs/specification.md`.",
                "- For product behavior, prefer "
                "[the specification](docs/specification.md).",
            )
            result = contract.check_document_contract(root)
            self.assertTrue(result.ok, result.issues)

        competing_source_owners = (
            "- For product behavior, prefer `docs/specification.md` and "
            "`docs/design.md`.",
            "- For product behavior, prefer `docs/specification.md` and "
            "`README.md` as owners.",
            "- For product behavior, do not merely use "
            "`docs/specification.md`.",
        )
        for replacement in competing_source_owners:
            with self.subTest(competing_owner=replacement), self.fixture() as root:
                self.replace(
                    root,
                    "AGENTS.md",
                    "- For product behavior, prefer `docs/specification.md`.",
                    replacement,
                )
                self.assertIn(
                    "authority_route",
                    self.codes(contract.check_document_contract(root)),
                )

        with self.fixture() as root:
            self.replace(
                root,
                "AGENTS.md",
                "- For product behavior, prefer `docs/specification.md`.",
                "- For product behavior, prefer `docs/specification.md` as a "
                "reference; use `docs/design.md` as the owner.",
            )
            self.assertIn(
                "authority_route",
                self.codes(contract.check_document_contract(root)),
            )

        with self.fixture() as root:
            self.replace(
                root,
                "AGENTS.md",
                "- For product behavior, prefer `docs/specification.md`.",
                "- For product behavior, prefer `docs/design.md`. Use "
                "`docs/specification.md` as a reference.",
            )
            self.assertIn(
                "authority_route",
                self.codes(contract.check_document_contract(root)),
            )

        with self.fixture() as root:
            self.replace(
                root,
                "AGENTS.md",
                "Before an implementation-affecting decision",
                "Before a decision that affects implementation",
            )
            result = contract.check_document_contract(root)
            self.assertTrue(result.ok, result.issues)

        with self.fixture() as root:
            self.replace(
                root,
                "AGENTS.md",
                "- For product behavior, prefer `docs/specification.md`.",
                "- For product behavior, `docs/specification.md` is not the "
                "owner; use `docs/design.md`.",
            )
            self.assertIn(
                "authority_route",
                self.codes(contract.check_document_contract(root)),
            )

        with self.fixture() as root:
            self.replace(
                root,
                "AGENTS.md",
                "- Follow `AGENTS.md` for agent behavior, safety, and workflow "
                "discipline.",
                "- Follow agent behavior, safety, and workflow <!-- `AGENTS.md`\n"
                "  -->",
            )
            self.assertIn(
                "authority_route",
                self.codes(contract.check_document_contract(root)),
            )

        with self.fixture() as root:
            self.replace(
                root,
                contract.AUTHORITY,
                "| Supported product behavior, public CLI/JSON, persistence, "
                "privacy, setup, Viewer, or current gate | Exact section in "
                "`docs/specification.md` |",
                "| Supported product behavior, public CLI/JSON, persistence, "
                "privacy, setup, Viewer, or current gate | Exact section in "
                "`docs/design.md` |",
            )
            self.assertIn(
                "authority_route",
                self.codes(contract.check_document_contract(root)),
            )

        with self.fixture() as root:
            self.replace(
                root,
                contract.AUTHORITY,
                "| Supported product behavior, public CLI/JSON, persistence, "
                "privacy, setup, Viewer, or current gate | Exact section in "
                "`docs/specification.md` |",
                "| Supported product behavior, public CLI/JSON, persistence, "
                "privacy, setup, Viewer, or current gate | Do not route to "
                "`docs/specification.md` |",
            )
            self.assertIn(
                "authority_route",
                self.codes(contract.check_document_contract(root)),
            )

        with self.fixture() as root:
            self.replace(
                root,
                contract.AUTHORITY,
                "| Supported product behavior, public CLI/JSON, persistence, "
                "privacy, setup, Viewer, or current gate | Exact section in "
                "`docs/specification.md` |",
                "| Supported product behavior, public CLI/JSON, persistence, "
                "privacy, setup, Viewer, or current gate | Exact section in "
                "`docs/specification.md`; `README.md` is also an owner |",
            )
            self.assertIn(
                "authority_route",
                self.codes(contract.check_document_contract(root)),
            )

        with self.fixture() as root:
            self.replace(
                root,
                contract.AUTHORITY,
                "| Supported product behavior, public CLI/JSON, persistence, "
                "privacy, setup, Viewer, or current gate | Exact section in "
                "`docs/specification.md` |",
                "| Supported product behavior, public CLI/JSON, persistence, "
                "privacy, setup, Viewer, or current gate | Exact section in "
                "`docs/design.md` <!-- `docs/specification.md` --> |",
            )
            self.assertIn(
                "authority_route",
                self.codes(contract.check_document_contract(root)),
            )

        with self.fixture() as root:
            self.replace(
                root,
                contract.AUTHORITY,
                "| Supported product behavior, public CLI/JSON, persistence, "
                "privacy, setup, Viewer, or current gate | Exact section in "
                "`docs/specification.md` |",
                "| Supported product behavior and published artifact Release "
                "identity | Exact section in `docs/specification.md` and "
                "`docs/release-install.md` |",
            )
            self.replace(
                root,
                contract.AUTHORITY,
                "| Published artifact, install, upgrade, tag, or Release identity | "
                "`docs/release-install.md` |",
                "| Unknown selective trigger | Unassigned |",
            )
            self.assertIn(
                "authority_route",
                self.codes(contract.check_document_contract(root)),
            )

    def test_registry_v4_is_closed_and_key_order_independent(self):
        with self.fixture() as root:
            registry = self.registry(root)
            self.assertEqual(registry["schema"], "taskgov-document-authority-v4")
            self.write_registry(root, dict(reversed(tuple(registry.items()))))
            result = contract.check_document_contract(root)
            self.assertTrue(result.ok, result.issues)

        def missing_owner(registry: dict[str, object]) -> None:
            registry.pop("documentation_sequence")

        def unknown_member(registry: dict[str, object]) -> None:
            registry["unknown_owner"] = {"path": "docs/unknown.md"}

        def wrong_doc_status(registry: dict[str, object]) -> None:
            sequence = registry["documentation_sequence"]
            assert isinstance(sequence, dict)
            sequence["current_units"] = ["TG-DOC.2"]

        for name, mutate in (
            ("missing_owner", missing_owner),
            ("unknown_member", unknown_member),
            ("wrong_doc_status", wrong_doc_status),
        ):
            with self.subTest(name=name), self.fixture() as root:
                registry = self.registry(root)
                mutate(registry)
                self.write_registry(root, registry)
                self.assertIn(
                    "authority_registry",
                    self.codes(contract.check_document_contract(root)),
                )

        with self.fixture() as root:
            self.replace(
                root,
                contract.AUTHORITY,
                '  "schema": "taskgov-document-authority-v4",',
                '  "schema": "taskgov-document-authority-v4",\n'
                '  "schema": "taskgov-document-authority-v4",',
            )
            self.assertIn(
                "authority_registry",
                self.codes(contract.check_document_contract(root)),
            )

    def test_required_roles_and_live_state_exclusion_are_structural(self):
        with self.fixture() as root:
            self.append(
                root,
                "docs/specification.md",
                "\n# Duplicate Product Owner\n",
            )
            self.assertIn(
                "document_role",
                self.codes(contract.check_document_contract(root)),
            )

        with self.fixture() as root:
            self.append(
                root,
                "plan.md",
                "\n## Live Task Snapshot\n\n"
                "task_id: tg_task_bf2aa245019f5c9f\n"
                "status: in_progress\n"
                "review_target_generation: 7\n"
                "tg_review_receipt_0123456789abcdef\n",
            )
            self.assertIn(
                "volatile_state",
                self.codes(contract.check_document_contract(root)),
            )

        live_target_values = (
            ("review_target_kind", "git_snapshot"),
            ("review_target_kind", "null"),
            ("review_target_value", "sha256:" + ("a" * 64)),
            ("review_target_value", "r1"),
            ("review_target_value", "null"),
            ("review_target_base_revision", "b" * 40),
            ("review_target_base_revision", "null"),
            ("review_target_generation", "0"),
            ("review_target_generation", "7"),
        )
        for field, value in live_target_values:
            with self.subTest(live_target_field=field), self.fixture() as root:
                self.append(root, "plan.md", f"\n{field}: {value}\n")
                self.assertIn(
                    "volatile_state",
                    self.codes(contract.check_document_contract(root)),
                )

        live_container_forms = (
            "The current Task is TG-M24.1.",
            "The [current Task](docs/authority.md) is TG-M24.1.",
            "[TG-M24.1](docs/authority.md \"owner\") is current.",
            "- review_target_generation: 7",
            "review_target_generation = 7",
            "| review_target_generation | 7 |",
            "The current Task is `TG-M24.1`.",
            "The current Task is [TG-M24.1](docs/authority.md).",
            'The current Task is [TG-M24.1](docs/authority.md "owner").',
            "The current Task is **TG-M24.1**.",
            "- `review_target_generation`: `7`",
            "| `review_target_generation` | `0` |",
            "| `review_target_generation` | `7` |",
        )
        for statement in live_container_forms:
            with self.subTest(live_container=statement), self.fixture() as root:
                self.append(root, "plan.md", f"\n{statement}\n")
                self.assertIn(
                    "volatile_state",
                    self.codes(contract.check_document_contract(root)),
                )

        live_status_forms = (
            "status: in_progress",
            "status = done",
            "- `status`: `done`",
            "| `status` | `done` |",
            "**status**: in_progress",
            "status: **in_progress**",
            "**status: in_progress**",
            "[status](docs/authority.md): done",
            "blocked_reason: waiting_for_user",
            "pause_reason: user_requested",
            "completed_at: 2026-08-07T00:00:00Z",
            "completion_commit_hash: " + ("c" * 40),
        )
        for statement in live_status_forms:
            with self.subTest(live_status=statement), self.fixture() as root:
                self.append(root, "plan.md", f"\n{statement}\n")
                self.assertIn(
                    "volatile_state",
                    self.codes(contract.check_document_contract(root)),
                )

        unit_live_forms = (
            "TG-DOC.2 status: done",
            "The status of TG-DOC.2 is done.",
            "TG-DOC.2 uses review_target_generation: 7.",
            "TG-DOC.2 uses review_target_generation = 7.",
            "TG-DOC.2 uses "
            "[review_target_generation](docs/authority.md): 7.",
            "TG-M24.1 is next.",
            "Current Task: TG-DOC.2",
            "Next Task: TG-M24.1",
            "| Current Task | TG-DOC.2 |",
            "| Task | Status |\n|---|---|\n| TG-DOC.2 | done |",
            "| Task | review_target_generation |\n"
            "|---|---|\n"
            "| TG-M24.1 | 7 |",
            "**review_target_generation: 7**",
        )
        for statement in unit_live_forms:
            with self.subTest(unit_live=statement), self.fixture() as root:
                self.append(root, "plan.md", f"\n{statement}\n")
                self.assertIn(
                    "volatile_state",
                    self.codes(contract.check_document_contract(root)),
                )

        table_target_values = (
            ("review_target_kind", "git_snapshot"),
            ("review_target_value", "r1"),
            ("review_target_base_revision", "abc123"),
            ("review_target_generation", "7"),
        )
        for field, value in table_target_values:
            table = (
                f"| Task | {field} |\n"
                "|---|---|\n"
                f"| TG-M24.1 | {value} |"
            )
            with self.subTest(table_target_field=field):
                self.assertTrue(contract._has_unit_live_state(table))
        self.assertFalse(
            contract._has_unit_live_state(
                "| Task | review_target_generation |\n"
                "|---|---|\n"
                "| string | integer |"
            )
        )

        with self.fixture() as root:
            self.append(
                root,
                "plan.md",
                "\nThe current Task is [TG-M24.1][current-task].\n\n"
                "[current-task]: docs/authority.md \"owner\"\n",
            )
            self.assertIn(
                "volatile_state",
                self.codes(contract.check_document_contract(root)),
            )

        with self.fixture() as root:
            self.append(root, "plan.md", "\n`TG-M24.1` is current.\n")
            self.assertIn(
                "document_role",
                self.codes(contract.check_document_contract(root)),
            )

        with self.fixture() as root:
            self.append(root, "plan.md", "\n**TG-M24.1** is current.\n")
            self.assertIn(
                "document_role",
                self.codes(contract.check_document_contract(root)),
            )

        with self.fixture() as root:
            self.append(
                root,
                "plan.md",
                "\n| review_target_generation | integer|string |\n"
                "| status | string |\n",
            )
            result = contract.check_document_contract(root)
            self.assertTrue(result.ok, result.issues)

        for subject in ("TG-M24.1", "TG-DOC.2", "TG-M21.5"):
            with self.subTest(current_subject=subject), self.fixture() as root:
                self.append(root, "plan.md", f"\n{subject} is current.\n")
                self.assertIn(
                    "document_role",
                    self.codes(contract.check_document_contract(root)),
                )

        with self.fixture() as root:
            self.replace(
                root,
                contract.M24,
                "# TG-M24 Verification Runner Conditional Execution Contract",
                "# TG-M24 Verification Runner Current Execution Contract",
            )
            self.assertIn(
                "document_role",
                self.codes(contract.check_document_contract(root)),
            )

        with self.fixture() as root:
            self.replace(
                root,
                contract.AUTHORITY,
                "# Repository Authority Index",
                "# Repository Non-Authority Index",
            )
            self.assertIn(
                "document_role",
                self.codes(contract.check_document_contract(root)),
            )

        with self.fixture() as root:
            self.replace(
                root,
                contract.AUTHORITY,
                "# Repository Authority Index",
                "# Repository Not an Authority Index",
            )
            self.assertIn(
                "document_role",
                self.codes(contract.check_document_contract(root)),
            )

        with self.fixture() as root:
            self.replace(
                root,
                contract.M24,
                "# TG-M24 Verification Runner Conditional Execution Contract",
                "# TG-M24 Verification Runner Not Conditional Execution Contract",
            )
            self.assertIn(
                "document_role",
                self.codes(contract.check_document_contract(root)),
            )

        with self.fixture() as root:
            self.replace(
                root,
                contract.M24,
                "# TG-M24 Verification Runner Conditional Execution Contract",
                "TG-M24 Verification Runner Conditional Execution Contract",
            )
            self.append(
                root,
                contract.M24,
                "\n> # TG-M24 Verification Runner Conditional Execution Contract\n",
            )
            self.assertIn(
                "document_role",
                self.codes(contract.check_document_contract(root)),
            )

        with self.fixture() as root:
            self.replace(
                root,
                contract.M24,
                "CONDITIONAL FORMAL AUTHORITY",
                "NOT CONDITIONAL FORMAL AUTHORITY",
            )
            self.replace(
                root,
                contract.M24,
                "ACCEPTED BUT INACTIVE",
                "ACCEPTED BUT NOT INACTIVE",
            )
            self.assertIn(
                "document_role",
                self.codes(contract.check_document_contract(root)),
            )

        with self.fixture() as root:
            self.replace(
                root,
                contract.M24,
                "ACCEPTED BUT INACTIVE.",
                "ACCEPTED BUT <!-- INACTIVE -->.",
            )
            self.assertIn(
                "document_role",
                self.codes(contract.check_document_contract(root)),
            )

        with self.fixture() as root:
            self.replace(
                root,
                contract.M24,
                "CONDITIONAL FORMAL AUTHORITY — ACCEPTED BUT INACTIVE.",
                "FORMAL AUTHORITY.\n"
                "> - ~~~text\n"
                ">   CONDITIONAL ACCEPTED INACTIVE\n"
                ">   ~~~",
            )
            self.assertIn(
                "document_role",
                self.codes(contract.check_document_contract(root)),
            )

        with self.fixture() as root:
            self.replace(
                root,
                contract.M24,
                "ACCEPTED BUT INACTIVE",
                "FORMERLY ACCEPTED BUT NO LONGER INACTIVE",
            )
            self.assertIn(
                "document_role",
                self.codes(contract.check_document_contract(root)),
            )

        with self.fixture() as root:
            self.replace(
                root,
                contract.M23,
                "NO\n> TG-M23 UNIT IS CURRENT",
                "NOT REJECTED; A UNIT IS CURRENT",
            )
            self.assertIn(
                "document_role",
                self.codes(contract.check_document_contract(root)),
            )

        with self.fixture() as root:
            self.replace(
                root,
                contract.M23,
                "NO\n> TG-M23 UNIT IS CURRENT",
                "NO TG-M23 UNIT IS CURRENT; A UNIT IS CURRENT",
            )
            self.assertIn(
                "document_role",
                self.codes(contract.check_document_contract(root)),
            )

    def test_doc_and_m24_identity_order_and_dependency_are_canonical(self):
        sequence_mutations = (
            (
                contract.M22,
                "tg_task_88bfe19eb6cffe2e",
                "tg_task_3333333333333333",
            ),
            (
                contract.M23,
                "| TG-M23.3 / 30 | `tg_task_0ada32d2b4f9759d` | accepted TG-M23.2 |",
                "| TG-M23.3 / 30 | `tg_task_0ada32d2b4f9759d` | accepted TG-M23.1 |",
            ),
            (
                contract.M24,
                "| TG-M24.1 / 10 | `tg_task_29aa63124900ad95` | accepted TG-DOC.2 |",
                "| TG-M24.1 / 10 | `tg_task_2222222222222222` | accepted TG-DOC.2 |",
            ),
            (
                contract.M24,
                "| TG-M24.1 / 10 | `tg_task_29aa63124900ad95` | accepted TG-DOC.2 |",
                "| TG-M24.1 / 11 | `tg_task_29aa63124900ad95` | accepted TG-DOC.2 |",
            ),
            (
                contract.M24,
                "| TG-M24.1 / 10 | `tg_task_29aa63124900ad95` | accepted TG-DOC.2 |",
                "| TG-M24.1 / 10 | `tg_task_29aa63124900ad95` | accepted TG-M23.3 |",
            ),
        )
        documentation_row_mutations = (
            (
                "doc2_task",
                DOC2_ROW.replace(
                    "`tg_task_bf2aa245019f5c9f`",
                    "`tg_task_0000000000000000`",
                ),
            ),
            (
                "doc2_lane",
                DOC2_ROW.replace(
                    "`TG-M23-DERIVED-EVIDENCE`",
                    "`TG-DOC-LIFECYCLE`",
                ),
            ),
            ("doc2_order", DOC2_ROW.replace("TG-DOC.2 / 40", "TG-DOC.2 / 41")),
            (
                "doc2_dependency",
                DOC2_ROW.replace("accepted TG-M23.3", "accepted TG-M23.2"),
            ),
            (
                "doc2_status",
                DOC2_ROW.replace(
                    "accepted predecessor; required before TG-M24.1",
                    "inactive predecessor; required before TG-M24.1",
                ),
            ),
            (
                "doc3_task",
                DOC3_ROW.replace(
                    "`tg_task_99371b8db2d43eb2`",
                    "`tg_task_1111111111111111`",
                ),
            ),
            (
                "doc3_lane",
                DOC3_ROW.replace(
                    "`TG-DOC-LIFECYCLE`",
                    "`TG-M23-DERIVED-EVIDENCE`",
                ),
            ),
            ("doc3_order", DOC3_ROW.replace("TG-DOC.3 / 20", "TG-DOC.3 / 21")),
            (
                "doc3_dependency",
                DOC3_ROW.replace(
                    "accepted TG-M24.4 and accepted TG-DOC.2",
                    "accepted TG-M24.4",
                ),
            ),
            (
                "doc3_status",
                DOC3_ROW.replace("inactive post-M24", "accepted post-M24"),
            ),
        )

        for relative, old, new in sequence_mutations:
            with self.subTest(relative=relative, mutation=new), self.fixture() as root:
                self.replace(root, relative, old, new)
                self.assertIn(
                    "sequence_contract",
                    self.codes(contract.check_document_contract(root)),
                )

        for name, mutated_row in documentation_row_mutations:
            with self.subTest(documentation_row=name), self.fixture() as root:
                canonical = DOC2_ROW if name.startswith("doc2_") else DOC3_ROW
                self.replace(root, "plan.md", canonical, mutated_row)
                self.assertIn(
                    "sequence_contract",
                    self.codes(contract.check_document_contract(root)),
                )

        with self.fixture() as root:
            self.replace(
                root,
                "plan.md",
                "| Unit/order | Task | Lane | Dependency | "
                "Authority status and successor gate |",
                "| Unit/order | Task | Dependency | Lane | "
                "Authority status and successor gate |",
            )
            self.assertIn(
                "sequence_contract",
                self.codes(contract.check_document_contract(root)),
            )

    def test_history_first_declaration_and_exactly_once_index_are_structural(self):
        long_heading = "# " + ("H" * 2_100)
        valid_capture = (
            long_heading
            + "\n\n> [!CAUTION]\n"
            "> NON-AUTHORITATIVE HISTORY. Fixture lineage only.\n\n"
            "Captured body.\n"
        )
        with self.fixture() as root:
            self.add_history_capture(
                root,
                "v0.12.0/semantic-fixture.md",
                valid_capture,
                index_count=1,
            )
            result = contract.check_document_contract(root)
            self.assertTrue(result.ok, result.issues)

        hidden_declarations = (
            (
                "html_comment",
                "<!-- NON-AUTHORITATIVE HISTORY -->\n",
            ),
            (
                "inline_code",
                "`NON-AUTHORITATIVE HISTORY`\n",
            ),
            (
                "quoted_html_comment",
                "# Capture\n\n> [!CAUTION]\n"
                "> <!-- NON-AUTHORITATIVE HISTORY -->\n",
            ),
            (
                "quoted_fence",
                "# Capture\n\n> [!CAUTION]\n> ```text\n"
                "> NON-AUTHORITATIVE HISTORY\n> ```\n",
            ),
            (
                "quoted_raw_html",
                "# Capture\n\n> [!CAUTION]\n> <pre>\n"
                "> NON-AUTHORITATIVE HISTORY\n> </pre>\n",
            ),
            (
                "quoted_html_close_before_blank",
                "# Capture\n\n> [!CAUTION]\n> <div>\n> </div>\n"
                "> NON-AUTHORITATIVE HISTORY\n>\n",
            ),
            (
                "quoted_type7_attribute",
                "# Capture\n\n> [!CAUTION]\n> <span title=\"x>y\">\n"
                "> NON-AUTHORITATIVE HISTORY\n>\n",
            ),
            (
                "quoted_list_container",
                "# Capture\n\n> [!CAUTION]\n> - <div>\n"
                ">   NON-AUTHORITATIVE HISTORY\n>\n",
            ),
            (
                "quoted_list_tilde_fence",
                "# Capture\n\n> [!CAUTION]\n> - ~~~text\n"
                ">   NON-AUTHORITATIVE HISTORY\n>   ~~~\n",
            ),
            (
                "quoted_fence_list_content",
                "# Capture\n\n> [!CAUTION]\n> ~~~text\n"
                "> - ~~~\n> NON-AUTHORITATIVE HISTORY\n> ~~~\n",
            ),
            (
                "quoted_list_fence_reentry",
                "# Capture\n\n> [!CAUTION]\n> - ~~~text\n"
                "> ~~~\n>   ~~~\n> NON-AUTHORITATIVE HISTORY\n> ~~~\n",
            ),
        )
        for name, content in hidden_declarations:
            with self.subTest(hidden_declaration=name), self.fixture() as root:
                self.add_history_capture(
                    root,
                    f"v0.12.0/{name}.md",
                    content,
                    index_count=1,
                )
                self.assertIn(
                    "history_banner",
                    self.codes(contract.check_document_contract(root)),
                )

        with self.fixture() as root:
            self.replace(
                root,
                contract.HISTORY_INDEX,
                "# Historical Documentation Index\n\n> [!CAUTION]",
                "# Historical Documentation Index\n\nOrdinary prose.\n\n> [!CAUTION]",
            )
            self.assertIn(
                "history_banner",
                self.codes(contract.check_document_contract(root)),
            )

        with self.fixture() as root:
            self.replace(
                root,
                contract.HISTORY_INDEX,
                "> Files indexed here are preserved implementation history, not current\n"
                "> authority. Use the active",
                "> Files indexed here are not current release material; they remain\n"
                "> binding authority. Use the active",
            )
            self.assertIn(
                "history_banner",
                self.codes(contract.check_document_contract(root)),
            )

        with self.fixture() as root:
            self.add_history_capture(
                root,
                "v0.12.0/negated-role.md",
                "# Capture\n\n> This is not non-authoritative history.\n",
                index_count=1,
            )
            self.assertIn(
                "history_banner",
                self.codes(contract.check_document_contract(root)),
            )

        with self.fixture() as root:
            self.add_history_capture(
                root,
                "v0.12.0/article-negated-role.md",
                "# Capture\n\n> This is not a non-authoritative history.\n",
                index_count=1,
            )
            self.assertIn(
                "history_banner",
                self.codes(contract.check_document_contract(root)),
            )

        with self.fixture() as root:
            self.add_history_capture(
                root,
                "v0.12.0/contradictory-role.md",
                "# Capture\n\n"
                "> NON-AUTHORITATIVE HISTORY. This is the authoritative source.\n",
                index_count=1,
            )
            self.assertIn(
                "history_banner",
                self.codes(contract.check_document_contract(root)),
            )

        for index_count in (0, 2):
            with self.subTest(index_count=index_count), self.fixture() as root:
                self.add_history_capture(
                    root,
                    "v0.12.0/index-count-fixture.md",
                    valid_capture,
                    index_count=index_count,
                )
                self.assertIn(
                    "history_index",
                    self.codes(contract.check_document_contract(root)),
                )

        with self.fixture() as root:
            unexpected = root / "docs" / "history" / "v0.12.0" / "capture.txt"
            unexpected.write_bytes(b"not an indexed Markdown capture\n")
            self.assertIn(
                "history_file",
                self.codes(contract.check_document_contract(root)),
            )

    def test_history_search_exclusion_is_semantic(self):
        with self.fixture() as root:
            self.append(root, ".ignore", "# Unrelated local rule\n/extra/\n")
            self.append(root, ".gitignore", "\n# Unrelated generated path\n/local/\n")
            result = contract.check_document_contract(root)
            self.assertTrue(result.ok, result.issues)

        with self.fixture() as root:
            self.replace(
                root,
                ".ignore",
                "/docs/history/\n",
                "!/docs/history/\n/docs/history/\n",
            )
            result = contract.check_document_contract(root)
            self.assertTrue(result.ok, result.issues)

        broad_negation_cases = (
            ("broad_final", "!/**\n", False),
            ("broad_then_reexclude", "!/**\n/docs/history/\n", True),
            ("unrelated_negation", "!/docs/current/**\n", True),
            ("version_subtree", "!/docs/history/v0.12.0/**\n", False),
            ("history_glob", "!/docs/hist*/**\n", False),
            ("trailing_spaces", "!/docs/history/**   \n", False),
            ("shallow_docs_markdown", "!/docs/*.md\n", True),
        )
        for name, suffix, should_pass in broad_negation_cases:
            with self.subTest(ignore_case=name), self.fixture() as root:
                self.append(root, ".ignore", suffix)
                result = contract.check_document_contract(root)
                if should_pass:
                    self.assertTrue(result.ok, result.issues)
                else:
                    self.assertIn("search_policy", self.codes(result))

        with self.fixture() as root:
            self.append(
                root,
                ".ignore",
                "!/**\n"
                "/docs/history/README.md\n"
                "/docs/history/_taskgov_probe_.md\n"
                "/docs/history/v0/_taskgov_probe_.md\n",
            )
            self.assertIn(
                "search_policy",
                self.codes(contract.check_document_contract(root)),
            )

        with self.fixture() as root:
            self.append(root, ".ignore", "!/docs/history/\n")
            self.assertIn(
                "search_policy",
                self.codes(contract.check_document_contract(root)),
            )

        with self.fixture() as root:
            self.replace(root, ".ignore", "/docs/history/\n", "/docs/other/\n")
            self.assertIn(
                "search_policy",
                self.codes(contract.check_document_contract(root)),
            )

        for name, ineffective in (
            ("leading_space", " /docs/history/\n"),
            ("backslash", "/docs\\history/\n"),
        ):
            with self.subTest(ineffective_rule=name), self.fixture() as root:
                self.replace(root, ".ignore", "/docs/history/\n", ineffective)
                self.assertIn(
                    "search_policy",
                    self.codes(contract.check_document_contract(root)),
                )

    def test_required_documents_reject_invalid_encoding_and_framing(self):
        mutations = (
            ("invalid_utf8", lambda raw: b"\xff" + raw),
            ("utf8_bom", lambda raw: b"\xef\xbb\xbf" + raw),
            ("missing_final_lf", lambda raw: raw.rstrip(b"\n")),
        )
        for name, mutate in mutations:
            with self.subTest(encoding_case=name), self.fixture() as root:
                path = root / "README.md"
                path.write_bytes(mutate(path.read_bytes()))
                self.assertIn(
                    "document_encoding",
                    self.codes(contract.check_document_contract(root)),
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


if __name__ == "__main__":
    unittest.main()
