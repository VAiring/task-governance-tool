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
    "docs/releases/v0.13.0.md",
    "task-governance-tool/LICENSE",
)
DOC2_ROW = (
    "| TG-DOC.2 / 40 | `tg_task_bf2aa245019f5c9f` | "
    "`TG-M23-DERIVED-EVIDENCE` | accepted TG-M23.3 | "
    "accepted predecessor; required before TG-M24.R1 |"
)
DOC3_ROW = (
    "| TG-DOC.3 / 20 | `tg_task_99371b8db2d43eb2` | "
    "`TG-DOC-LIFECYCLE` | accepted TG-M24.CP4 and accepted TG-DOC.2 | "
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

    def test_m24_trusted_local_boundary_is_explicit_and_closed(self):
        mutations = (
            (
                "Eligibility is restricted to a trusted-local repository and explicit opt-in.",
                "Eligibility includes any repository and automatic execution.",
            ),
            (
                "An untrusted or external target uses the M21 manual verification fallback.",
                "An untrusted or external target uses the Runner.",
            ),
            (
                "Launch uses fixed argv, shell=false, and a credential-excluding environment.",
                "Launch uses a command string, shell=true, and the ambient environment.",
            ),
            (
                "A Job Object, timeout, and bounded output constrain the process tree.",
                "The child process is launched without a process-tree or output bound.",
            ),
            (
                "Private temporary materialization is single-owner and cleanup is blocking.",
                "Temporary materialization may be shared and cleanup is advisory.",
            ),
            (
                "Raw stdout and stderr are transient and are never persisted; only the closed Runner outcome is persisted.",
                "Raw stdout and stderr may be persisted for diagnosis.",
            ),
            (
                "The Runner does not claim network isolation, hostile-code containment, or zero capability.",
                "The Runner provides hostile-code containment and zero capability.",
            ),
            (
                "Loading this contract does not activate product code or a Runner runtime.",
                "Loading this contract activates the Runner runtime.",
            ),
            (
                "Candidate C, B-to-C, LPAC, AppContainer, ETW, and registry recovery are not current M24 gates.",
                "Candidate C, LPAC, AppContainer, and ETW are current M24 gates.",
            ),
            (
                "Runner-slice module registry and acyclic dependency graph",
                "open Runner-slice module suggestions with cyclic dependencies",
            ),
            (
                "`cli.py` may call only `verification_runner_service.py`",
                "`cli.py` may call the process and OS adapters directly",
            ),
            (
                "Repository and persistence modules never launch\n"
                "or import the process or OS adapter.",
                "Repository and persistence modules may launch or import the process or OS adapter.",
            ),
            (
                "closed typed bounded request plus its local Boolean cancellation signal",
                "untyped open request plus an arbitrary callback",
            ),
            (
                "returns only the closed bounded sanitized result, opens no canonical state",
                "returns raw process state and opens canonical state",
            ),
            (
                "`verification_runner_service.py` alone combines the process",
                "Multiple modules combine the process",
            ),
            (
                "No raw output, argv, environment, credential,",
                "Raw output, argv, environment, and credentials may persist,",
            ),
            (
                "logical request/result records add no serializer, IPC, worker,",
                "logical request/result records activate a serializer, IPC, and worker,",
            ),
            (
                "are physically absent after accepted R4A",
                "remain available after accepted R4A",
            ),
            (
                "dependency-pure, legacy-stable value-model foundation",
                "repository-coupled value-model foundation",
            ),
            (
                "supplied by accepted R4V",
                "deferred to inactive R4B",
            ),
            (
                "transitional nonconformance routed to R4B",
                "accepted current dependency conformance",
            ),
            (
                "changed no R2A/R2B disposition or action selector",
                "changed R2A/R2B dispositions and action selectors",
            ),
        )
        for old, new in mutations:
            with self.subTest(old=old), self.fixture() as root:
                self.replace(root, contract.M24, old, new)
                self.assertIn(
                    "m24_trusted_local_authority_sync",
                    self.codes(contract.check_document_contract(root)),
                )

        design_mutations = (
            (
                "## Accepted TG-M24.R2C Trusted-Local Runner Architecture Boundary",
                "## Proposed TG-M24.R2C Trusted-Local Runner Architecture Boundary",
            ),
            (
                "| `value_model` | `verification_runner.py` |",
                "| `value_model` | `verification_runner.py`, `storage.py` |",
            ),
            (
                "Parent orchestration; sole ownership of opt-in and eligibility,",
                "Parent orchestration; not sole ownership of opt-in and eligibility,",
            ),
            (
                "Evidence and terminal persistence",
                "Evidence and no terminal persistence",
            ),
            (
                "and final cleanup acceptance.",
                "and no final cleanup acceptance.",
            ),
            (
                "current audit-only integration is owned by 2C.",
                "current audit-only integration is owned by 2C and TG-M24.CP4.",
            ),
            (
                "any retained Runner-specific direct process/native edge is repaired by R4B.",
                "any retained Runner-specific direct process/native edge is repaired by R4A and R4B.",
            ),
            (
                "Accepted R4V repairs the dependency-pure legacy-stable foundation and reverse edge;",
                "Accepted R4A repairs the dependency-pure legacy-stable foundation and reverse edge;",
            ),
            (
                "process_adapter -> os_adapter",
                "os_adapter -> process_adapter",
            ),
            (
                "| `value_model` | `verification_runner.py` | Pure closed Runner "
                "identifiers, bounded codes, value validation, and domain encoding used "
                "across the boundary. | none |",
                "| `value_model` | `verification_runner.py` | Pure closed Runner "
                "identifiers, bounded codes, value validation, and domain encoding used "
                "across the boundary. | `service` |",
            ),
            (
                "materialized_root, scratch_root, clean_environment, steps, cancel_signal",
                "materialized_root, scratch_root, clean_environment, steps, callback",
            ),
            (
                "total_process_count, process_zero, handles_closed, raw_output_discarded,",
                "total_process_count, raw_output, private_path, exception_body,",
            ),
            (
                "attempt_id = ASCII /tg_verification_runner_attempt_[0-9a-f]{16}/ (47 bytes)",
                "attempt_id = unrestricted caller text",
            ),
            (
                "result_code = ASCII /[a-z][a-z0-9_]{0,63}/ (1..64 bytes)",
                "result_code = unrestricted adapter text",
            ),
            (
                "result_outcome = result_code; result_reason = null or result_code",
                "result_outcome = arbitrary text; result_reason = arbitrary text",
            ),
            (
                "absolute_path = well-formed Unicode, absolute normalized Windows path, "
                "no NUL or Unicode Cc, no \".\" or \"..\" segment, 1..4096 UTF-8 bytes "
                "and 1..4096 UTF-16 code units",
                "absolute_path = any path with no byte or traversal bound",
            ),
            (
                "path_ownership = executable is a parent-verified fixed absolute "
                "package-runtime identity outside materialized_root and scratch_root with "
                "no PATH lookup; materialized_root and scratch_root are distinct target "
                "and scratch children of one owned attempt root; no symlink or reparse "
                "traversal",
                "path_ownership = executable and target use ambient shared paths",
            ),
            (
                "step_count = 1..16; argv_count_per_step = 0..64",
                "step_count = unbounded; argv_count_per_step = unbounded",
            ),
            (
                "clean_environment_keys = APPDATA, HOME, LOCALAPPDATA, "
                "PYTHONDONTWRITEBYTECODE, PYTHONNOUSERSITE, PYTHONUTF8, SystemRoot, "
                "TEMP, TMP, USERPROFILE, WINDIR",
                "clean_environment_keys = ambient process environment",
            ),
            (
                "result_step_count = 0..request.step_count and 0..16; "
                "result_step_ordinals = unique, request-ordered values in "
                "1..request.step_count",
                "result_step_count and ordinals are unbounded and unrelated to the request",
            ),
            (
                "observable payload is\n"
                "one Boolean; it contains no callback to SQLite, CLI policy, or a business gate,",
                "observable payload is arbitrary and may call SQLite or a business gate,",
            ),
            (
                "`verification_runner_service.py` is the single cleanup-\n"
                "acceptance owner:",
                "The process and lifecycle adapters are cleanup-acceptance owners:",
            ),
            (
                "`verification_runner_service.py` is the single cleanup-\n"
                "acceptance owner:",
                "`verification_runner_service.py` is not the single cleanup-\n"
                "acceptance owner:",
            ),
            (
                "it alone combines process-tree zero, handle closure, output\n"
                "discard, and private-tree absence",
                "it alone combines process-tree zero and private-tree absence",
            ),
            (
                "Raw output, argv, environment, credentials, private paths, exit codes, and\n"
                "exception bodies remain transient and are never stored.",
                "Raw process and private data may be stored.",
            ),
            (
                "Raw output, argv, environment, credentials, private paths, exit codes, and\n"
                "exception bodies remain transient and are never stored.",
                "Raw output and exception bodies remain transient and are never stored.",
            ),
            (
                "Raw output, argv, environment, credentials, private paths, exit codes, and\n"
                "exception bodies remain transient and are never stored.",
                "Raw output, argv, environment, credentials, private paths, exit codes, and\n"
                "exception bodies do not remain transient and are never stored.",
            ),
            (
                "The records above define no serializer, file spool,\n"
                "queue, pipe, socket, RPC, worker, daemon, subprocess wrapper, supervisor,\n"
                "heartbeat, retry protocol, secondary state store, or second database\n"
                "connection.",
                "The records above define no serializer or second database\n"
                "connection.",
            ),
            (
                "The records above define no serializer, file spool,",
                "The records above do not establish that they define no serializer, file spool,",
            ),
            (
                "M25 separation requires later explicit authority; R2C adds no IPC,",
                "M25 separation requires later explicit authority; it does not establish "
                "that R2C adds no IPC,",
            ),
            (
                "were accepted R4A physical-deletion scope and are now physically\n"
                "absent; they are not architecture nodes. Retained dependency violations are\n"
                "R4B scope.",
                "are accepted architecture nodes with no successor owner.",
            ),
            (
                "No such residue remains\n"
                "in the standard test partition, which continues to contain only the three base\n"
                "lanes `fast`, `integration`, and `release`.",
                "Retired residue remains in an additional mandatory native lane.",
            ),
            (
                "R2C adds no IPC,\n"
                "process, schema, public CLI, Skill trigger, completion gate, or product behavior.",
                "R2C activates IPC, a process, a public CLI, and product behavior.",
            ),
            (
                "This remains a reliability and privacy boundary for trusted code, not a\n"
                "hostile-code sandbox or a claim of network isolation.",
                "This becomes a hostile-code security boundary for trusted code with\n"
                "network isolation.",
            ),
        )
        for old, new in design_mutations:
            with self.subTest(old=old), self.fixture() as root:
                self.replace(root, contract.DESIGN, old, new)
                self.assertIn(
                    "m24_r2c_architecture_boundary",
                    self.codes(contract.check_document_contract(root)),
                )

        with self.fixture() as root:
            self.replace(
                root,
                contract.DESIGN,
                "Parse and format the existing public surface and dispatch the Runner "
                "route to the parent service.",
                "Handle public input parsing and response formatting, then dispatch the "
                "Runner route solely through the parent service.",
            )
            self.replace(
                root,
                contract.DESIGN,
                "RunnerProcessRequestV1 = version, attempt_id, executable,\n"
                "  materialized_root, scratch_root, clean_environment, steps, cancel_signal",
                "RunnerProcessRequestV1 = version,\n"
                "  attempt_id, executable, materialized_root, scratch_root,\n"
                "  clean_environment, steps, cancel_signal",
            )
            self.replace(
                root,
                contract.DESIGN,
                "  step_count = 1..16; argv_count_per_step = 0..64",
                "  step_count = 1..16;\n  argv_count_per_step = 0..64",
            )
            self.replace(
                root,
                contract.DESIGN,
                "process_adapter -> os_adapter",
                "process_adapter\n  -> os_adapter",
            )
            result = contract.check_document_contract(root)
            self.assertTrue(result.ok, result.issues)

        with self.fixture() as root:
            self.replace(
                root,
                contract.M24,
                "## TG-M24.R4V Accepted Dependency-Pure Legacy-Stable Runner Value Model Foundation",
                "## TG-M24.R4V Inactive Dependency-Pure Legacy-Stable Runner Value Model Foundation",
            )
            self.assertIn(
                "m24_current_binding",
                self.codes(contract.check_document_contract(root)),
            )

        with self.fixture() as root:
            self.replace(
                root,
                contract.M24,
                "## TG-M24.R3B Accepted Evidence And Projection Compatibility Baseline",
                "## TG-M24.R3B Inactive Evidence And Projection Compatibility Baseline",
            )
            self.assertIn(
                "m24_current_binding",
                self.codes(contract.check_document_contract(root)),
            )

        with self.fixture() as root:
            self.replace(
                root,
                contract.M24,
                "## TG-M24.R4B Accepted Pre-Runner Core And Dependency Repair",
                "## TG-M24.R4B Inactive Pre-Runner Core And Dependency Repair",
            )
            self.assertIn(
                "m24_current_binding",
                self.codes(contract.check_document_contract(root)),
            )

        with self.fixture() as root:
            self.replace(
                root,
                contract.M24,
                "## TG-M24.R5 Accepted Fixed Diagnostic Residue Retirement",
                "## TG-M24.R5 Inactive Fixed Diagnostic Residue Retirement",
            )
            self.assertIn(
                "m24_current_binding",
                self.codes(contract.check_document_contract(root)),
            )

        with self.fixture() as root:
            self.replace(
                root,
                contract.M24,
                "## TG-M24.2A Accepted Trusted Plan And Exact Target Materialization",
                "## TG-M24.2A Inactive Trusted Plan And Exact Target Materialization",
            )
            self.assertIn(
                "m24_current_binding",
                self.codes(contract.check_document_contract(root)),
            )

        with self.fixture() as root:
            self.replace(
                root,
                contract.M24,
                "## TG-M24.2B Accepted Bounded Local Process Runner",
                "## TG-M24.2B Inactive Bounded Local Process Runner",
            )
            self.assertIn(
                "m24_current_binding",
                self.codes(contract.check_document_contract(root)),
            )

        with self.fixture() as root:
            self.replace(
                root,
                contract.M24,
                "## TG-M24.2C Accepted Shadow Observation And Evidence Capture",
                "## TG-M24.2C Inactive Shadow Observation And Evidence Capture",
            )
            self.assertIn(
                "m24_current_binding",
                self.codes(contract.check_document_contract(root)),
            )

        with self.fixture() as root:
            self.replace(
                root,
                contract.M24,
                "## TG-M24.2D Accepted Shadow Runner Integrated Acceptance",
                "## TG-M24.2D Inactive Shadow Runner Integrated Acceptance",
            )
            self.assertIn(
                "m24_current_binding",
                self.codes(contract.check_document_contract(root)),
            )

        with self.fixture() as root:
            self.replace(
                root,
                contract.M24,
                "## TG-M24.3A Accepted Schema-v21 Gate-Basis Contract",
                "## TG-M24.3A Inactive Schema-v21 Gate-Basis Contract",
            )
            self.assertIn(
                "m24_current_binding",
                self.codes(contract.check_document_contract(root)),
            )

        with self.fixture() as root:
            self.replace(
                root,
                contract.M24,
                "## TG-M24.3B Accepted Schema-v21 Persistence Foundation",
                "## TG-M24.3B Inactive Schema-v21 Persistence Foundation",
            )
            self.assertIn(
                "m24_current_binding",
                self.codes(contract.check_document_contract(root)),
            )

        with self.fixture() as root:
            self.replace(
                root,
                contract.M24,
                "## TG-M24.3C Accepted Runner Gate Integration And M21 Fallback",
                "## TG-M24.3C Inactive Runner Gate Integration And M21 Fallback",
            )
            self.assertIn(
                "m24_current_binding",
                self.codes(contract.check_document_contract(root)),
            )

        with self.fixture() as root:
            self.replace(
                root,
                contract.M24,
                "## TG-M24.4A Accepted Supported, Fallback, Failure, And Privacy Acceptance",
                "## TG-M24.4A Inactive Supported, Fallback, Failure, And Privacy Acceptance",
            )
            self.assertIn(
                "m24_current_binding",
                self.codes(contract.check_document_contract(root)),
            )

        with self.fixture() as root:
            self.replace(
                root,
                contract.M24,
                "## TG-M24.4B Accepted Legacy, Core, And Fresh-State Acceptance",
                "## TG-M24.4B Inactive Legacy, Core, And Fresh-State Acceptance",
            )
            self.assertIn(
                "m24_current_binding",
                self.codes(contract.check_document_contract(root)),
            )

        with self.fixture() as root:
            self.replace(
                root,
                contract.M24,
                "## TG-M24.4C Accepted v0.13 Package And Release-Candidate Acceptance",
                "## TG-M24.4C Inactive v0.13 Package And Release-Candidate Acceptance",
            )
            self.assertIn(
                "m24_current_binding",
                self.codes(contract.check_document_contract(root)),
            )

        with self.fixture() as root:
            self.replace(
                root,
                contract.M24,
                "## TG-M24.4D Accepted Verification Runner Integrated Acceptance",
                "## TG-M24.4D Inactive Verification Runner Integrated Acceptance",
            )
            self.assertIn(
                "m24_current_binding",
                self.codes(contract.check_document_contract(root)),
            )

        with self.fixture() as root:
            self.replace(
                root,
                contract.M24,
                "## TG-M24.CP4 Current Final No-Debt Repair Checkpoint And M25 Handoff",
                "## TG-M24.CP4 Inactive Final No-Debt Repair Checkpoint And M25 Handoff",
            )
            self.assertIn(
                "m24_current_binding",
                self.codes(contract.check_document_contract(root)),
            )

        with self.fixture() as root:
            self.replace(
                root,
                contract.M24,
                "<a id=\"tg-m24-r4a\"></a>",
                "## TG-M24.CP4 Current Duplicate Owner\n\n"
                "<a id=\"tg-m24-r4a\"></a>",
            )
            self.assertIn(
                "m24_current_binding",
                self.codes(contract.check_document_contract(root)),
            )

        with self.fixture() as root:
            self.replace(
                root,
                contract.M24,
                "## TG-M24.CP4 Current Final No-Debt Repair Checkpoint And M25 Handoff",
                "## TG-M24.CP4 Final No-Debt Repair Checkpoint And M25 Handoff Current",
            )
            result = contract.check_document_contract(root)
            self.assertTrue(result.ok, result.issues)

    def test_m24_trigger_route_is_structural(self):
        with self.fixture() as root:
            self.replace(
                root,
                contract.AUTHORITY,
                "Exact accepted predecessor, current unit, inactive unit, or superseded unit",
                "Exact accepted predecessor or inactive unit",
            )
            self.assertIn(
                "authority_route",
                self.codes(contract.check_document_contract(root)),
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

    def test_m24_3a_owner_routes_require_explicit_anchors(self):
        with self.fixture() as root:
            expected = "current-schema-v21-persistence-contract"
            alternate = f"{expected}-alternate"
            self.replace(
                root,
                "docs/specification.md",
                f'<a id="{expected}"></a>',
                f'<a id="{alternate}"></a>',
            )
            self.replace(
                root,
                contract.M24,
                f"../specification.md#{expected}",
                f"../specification.md#{alternate}",
            )
            codes = self.codes(contract.check_document_contract(root))
            self.assertIn("authority_route", codes)
            self.assertNotIn("link_anchor", codes)

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

    def test_registry_v5_is_closed_and_key_order_independent(self):
        with self.fixture() as root:
            registry = self.registry(root)
            self.assertEqual(registry["schema"], "taskgov-document-authority-v5")
            self.assertEqual(registry["conditional"], [])
            mixed = registry["mixed_execution"]
            self.assertIsInstance(mixed, list)
            m24 = next(
                item
                for item in mixed
                if isinstance(item, dict) and item.get("path") == contract.M24
            )
            self.assertEqual(m24["current_units"], ["TG-M24.CP4"])
            self.assertEqual(m24["inactive_units"], [])
            self.assertEqual(m24["superseded_units"], ["TG-M24.1B"])
            self.assertNotIn("TG-M24.1", m24["current_units"])
            self.assertNotIn("TG-M24.1A", m24["inactive_units"])
            self.assertNotIn("TG-M24.R2C", m24["inactive_units"])
            self.assertNotIn("TG-M24.R4A", m24["inactive_units"])
            self.assertNotIn("TG-M24.R4V", m24["current_units"])
            self.assertNotIn("TG-M24.R4V", m24["inactive_units"])
            self.assertNotIn("TG-M24.R4V", m24["superseded_units"])
            self.assertNotIn("TG-M24.R3B", m24["current_units"])
            self.assertNotIn("TG-M24.R3B", m24["inactive_units"])
            self.assertNotIn("TG-M24.R4B", m24["current_units"])
            self.assertNotIn("TG-M24.R4B", m24["inactive_units"])
            self.assertNotIn("TG-M24.R5", m24["current_units"])
            self.assertNotIn("TG-M24.R5", m24["inactive_units"])
            self.assertNotIn("TG-M24.2A", m24["current_units"])
            self.assertNotIn("TG-M24.2A", m24["inactive_units"])
            self.assertNotIn("TG-M24.2B", m24["current_units"])
            self.assertNotIn("TG-M24.2B", m24["inactive_units"])
            self.assertNotIn("TG-M24.2C", m24["inactive_units"])
            self.assertNotIn("TG-M24.2D", m24["current_units"])
            self.assertNotIn("TG-M24.2D", m24["inactive_units"])
            self.assertNotIn("TG-M24.3A", m24["inactive_units"])
            self.assertNotIn("TG-M24.3A", m24["current_units"])
            self.assertNotIn("TG-M24.4A", m24["inactive_units"])
            self.assertNotIn("TG-M24.4A", m24["current_units"])
            self.assertNotIn("TG-M24.4B", m24["inactive_units"])
            self.assertNotIn("TG-M24.4B", m24["current_units"])
            self.assertNotIn("TG-M24.4C", m24["inactive_units"])
            self.assertNotIn("TG-M24.4C", m24["current_units"])
            self.assertNotIn("TG-M24.4D", m24["current_units"])
            self.assertNotIn("TG-M24.4D", m24["inactive_units"])
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

        def wrong_m24_status(registry: dict[str, object]) -> None:
            mixed = registry["mixed_execution"]
            assert isinstance(mixed, list)
            m24 = next(
                item
                for item in mixed
                if isinstance(item, dict) and item.get("path") == contract.M24
            )
            m24["current_units"] = ["TG-M24.R2A"]

        for name, mutate in (
            ("missing_owner", missing_owner),
            ("unknown_member", unknown_member),
            ("wrong_doc_status", wrong_doc_status),
            ("wrong_m24_status", wrong_m24_status),
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
                '  "schema": "taskgov-document-authority-v5",',
                '  "schema": "taskgov-document-authority-v5",\n'
                '  "schema": "taskgov-document-authority-v5",',
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

        for subject in (
            "TG-M24.1",
            "TG-M24.R2A",
            "TG-M24.R2C",
            "TG-M24.R4A",
            "TG-M24.R4V",
            "TG-M24.R3B",
            "TG-M24.R4B",
            "TG-M24.R5",
            "TG-M24.2A",
            "TG-DOC.2",
            "TG-M21.5",
        ):
            with self.subTest(current_subject=subject), self.fixture() as root:
                self.append(root, "plan.md", f"\n{subject} is current.\n")
                self.assertIn(
                    "document_role",
                    self.codes(contract.check_document_contract(root)),
                )

        with self.fixture() as root:
            self.replace(
                root,
                contract.M22,
                "Current, accepted, inactive, and superseded TG-M24 membership is owned "
                "solely by\nthe [repository authority index](../authority.md) and that "
                "routed M24 contract.\nThis M22 document mirrors no downstream unit "
                "state and activates no downstream\nruntime.",
                "TG-M24.1 and its bounded TG-M24.1A correction are accepted "
                "predecessors;\ncurrent runtime qualification-and-supply authority "
                "belongs to TG-M24.1B,\nwhile TG-M24.2, TG-M24.3, and TG-M24.4 remain "
                "inactive. No TG-M24 Runner\nruntime is active.\nThis M22 document "
                "activates no downstream runtime.",
            )
            self.assertIn(
                "document_role",
                self.codes(contract.check_document_contract(root)),
            )

        with self.fixture() as root:
            self.replace(
                root,
                contract.M22,
                "Accepted downstream M24 schema-v20 storage already provides the "
                "dormant\nRunner-observation and Bundle-v2 null-Runner structural "
                "foundation. The routed\nM24 contract owns every durable mapping, "
                "writer, projection, and later tagged\nverification-basis/Bundle "
                "transition; M22 reserves only the producer/relation\nvocabulary and "
                "activates none of them.",
                "The M24 Runner may later add a runner-observation table and a new "
                "tagged\nverification-basis/bundle version.",
            )
            self.assertIn(
                "document_role",
                self.codes(contract.check_document_contract(root)),
            )

        with self.fixture() as root:
            self.replace(
                root,
                contract.EXECUTION_INDEX,
                "# Current And Conditional Execution Contract Index",
                "# Current And Conditional Reference Index",
            )
            self.assertIn(
                "document_role",
                self.codes(contract.check_document_contract(root)),
            )

        for relative in ("docs/specification.md", contract.DESIGN):
            with self.subTest(stale_verification_execution=relative), self.fixture() as root:
                self.replace(
                    root,
                    relative,
                    "a public command or Skill trigger for\n"
                    "standalone verification-command execution",
                    "verification-command execution",
                )
                self.assertIn(
                    "document_role",
                    self.codes(contract.check_document_contract(root)),
                )

        with self.fixture() as root:
            self.replace(
                root,
                "docs/specification.md",
                "The public `verification receipt add` command does not run the caller-attested\n"
                "verification represented by that Receipt, authenticate its caller or process,\n"
                "assess test quality, infer coverage, or prove the result or that the run\n"
                "actually exercised the copied target",
                "Taskgov does not run verification, authenticate the caller or process, assess\n"
                "test quality, infer coverage, or prove the result or that the run actually\n"
                "exercised the copied target",
            )
            self.assertIn(
                "document_role",
                self.codes(contract.check_document_contract(root)),
            )

        with self.fixture() as root:
            self.append(
                root,
                "plan.md",
                "\nInactive TG-M24.2A-through-TG-M24.CP4 implementation detail.\n",
            )
            self.assertIn(
                "document_role",
                self.codes(contract.check_document_contract(root)),
            )

        for stale_status_claim in (
            "TG-M24.2B owns current formal authority.",
            "TG-M24.2C owns current formal authority.",
            "TG-M24.2C remains inactive.",
        ):
            with self.subTest(stale_status_claim=stale_status_claim), self.fixture() as root:
                self.append(root, "plan.md", f"\n{stale_status_claim}\n")
                self.assertIn(
                    "document_role",
                    self.codes(contract.check_document_contract(root)),
                )

        with self.fixture() as root:
            self.replace(
                root,
                contract.M24,
                "# TG-M24 Verification Runner Current Execution Contract",
                "# TG-M24 Verification Runner Mixed Execution Contract",
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
                "# TG-M24 Verification Runner Current Execution Contract",
                "# TG-M24 Verification Runner Not Current Execution Contract",
            )
            self.assertIn(
                "document_role",
                self.codes(contract.check_document_contract(root)),
            )

        with self.fixture() as root:
            self.replace(
                root,
                contract.M24,
                "# TG-M24 Verification Runner Current Execution Contract",
                "TG-M24 Verification Runner Current Execution Contract",
            )
            self.append(
                root,
                contract.M24,
                "\n> # TG-M24 Verification Runner Current Execution Contract\n",
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
                "| TG-M24.R1 / 10 | `tg_task_8af2eee60acb0830` | reviewed R2 bootstrap boundary |",
                "| TG-M24.R1 / 10 | `tg_task_2222222222222222` | reviewed R2 bootstrap boundary |",
            ),
            (
                contract.M24,
                "| TG-M24.R1 / 10 | `tg_task_8af2eee60acb0830` | reviewed R2 bootstrap boundary |",
                "| TG-M24.R1 / 11 | `tg_task_8af2eee60acb0830` | reviewed R2 bootstrap boundary |",
            ),
            (
                contract.M24,
                "| TG-M24.R1 / 10 | `tg_task_8af2eee60acb0830` | reviewed R2 bootstrap boundary |",
                "| TG-M24.R1 / 10 | `tg_task_8af2eee60acb0830` | accepted TG-DOC.2 |",
            ),
            (
                contract.M24,
                "| TG-M24.R2B / 25 | `tg_task_ca8d0d81cd1962ab` | accepted TG-M24.R2A |",
                "| TG-M24.R2B / 25 | `tg_task_ca8d0d81cd1962ab` | accepted TG-M24.R1 |",
            ),
            (
                contract.M24,
                "| TG-M24.R4V / 45 | `tg_task_006bee9937e25af9` | accepted TG-M24.R4A |",
                "| TG-M24.R4V / 45 | `tg_task_0000000000000000` | accepted TG-M24.R4A |",
            ),
            (
                contract.M24,
                "| TG-M24.3A / 130 | `tg_task_2b7efe1c4545cca8` | accepted TG-M24.2D |",
                "| TG-M24.3A / 130 | `tg_task_0000000000000000` | accepted TG-M24.2D |",
            ),
            (
                contract.M24,
                "| TG-M24.3A / 130 | `tg_task_2b7efe1c4545cca8` | accepted TG-M24.2D |",
                "| TG-M24.3A / 131 | `tg_task_2b7efe1c4545cca8` | accepted TG-M24.2D |",
            ),
            (
                contract.M24,
                "| TG-M24.3B / 135 | `tg_task_1c3f41dc4bc88a68` | accepted TG-M24.3A |",
                "| TG-M24.3B / 135 | `tg_task_1c3f41dc4bc88a68` | accepted TG-M24.2D |",
            ),
            (
                contract.M24,
                "| TG-M24.3C / 138 | `tg_task_dc015144091f8e60` | accepted TG-M24.3B |",
                "| TG-M24.3C / 138 | `tg_task_dc015144091f8e60` | accepted TG-M24.3A |",
            ),
            (
                contract.M24,
                "| TG-M24.4A / 140 | `tg_task_0da786589eb5144a` | accepted TG-M24.3C |",
                "| TG-M24.4A / 140 | `tg_task_0da786589eb5144a` | accepted TG-M24.3B |",
            ),
            (
                contract.M24,
                "| TG-M24.CP4 / 180 | `tg_task_a9e1229d594594d4` | accepted TG-M24.4D |",
                "| TG-M24.CP4 / 180 | `tg_task_a9e1229d594594d4` | accepted TG-M24.4C |",
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
                    "accepted predecessor; required before TG-M24.R1",
                    "inactive predecessor; required before TG-M24.R1",
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
                    "accepted TG-M24.CP4 and accepted TG-DOC.2",
                    "accepted TG-M24.CP4",
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

        with self.fixture() as root:
            self.replace(
                root,
                contract.M24,
                '<a id="tg-m24-r2a"></a>',
                '<a id="tg-m24-r2b"></a>',
            )
            self.assertIn(
                "sequence_contract",
                self.codes(contract.check_document_contract(root)),
            )

        with self.fixture() as root:
            self.replace(
                root,
                contract.M24,
                '<a id="tg-m24-r4v"></a>',
                '<a id="tg-m24-r4v-swap"></a>',
            )
            self.replace(
                root,
                contract.M24,
                '<a id="tg-m24-r3a"></a>',
                '<a id="tg-m24-r4v"></a>',
            )
            self.replace(
                root,
                contract.M24,
                '<a id="tg-m24-r4v-swap"></a>',
                '<a id="tg-m24-r3a"></a>',
            )
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
