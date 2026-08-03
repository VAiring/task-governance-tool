"""Offline checks for this repository's fixed documentation contract."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
sys.dont_write_bytecode = True
DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[1]
AUTHORITY = "docs/authority.md"
HISTORY_INDEX = "docs/history/README.md"
RELEASE_INSTALL = "docs/release-install.md"
BASELINE_COMMIT = "695b240178681a072b5cbd73845dff8e31a281d6"
LEGACY_LINES = 7_404
LEGACY_BYTES = 428_058
MIN_REDUCTION_BP = 9_000
PRE_M22_SHA256 = "426d27bb9189349439b7a2c5764bad0b2035d9cdd6dfd40b7152d45d2054728f"
TRIGGER_ROUTING_SHA256 = "f07ba62adfb4af8ccb9014e7f194713794302b7be7af7b5c87c7c3437a04fa74"
EXECUTION_INDEX = "docs/execution-contracts/README.md"
M22 = "docs/execution-contracts/tg-m22-evidence-ledger.md"
M23 = "docs/execution-contracts/tg-m23-derived-evidence.md"
M24 = "docs/execution-contracts/tg-m24-verification-runner.md"
BUDGETS = {
    "AGENTS.md": (500, 30_000), AUTHORITY: (160, 16_000),
    "docs/specification.md": (2_500, 150_000), "docs/design.md": (2_450, 135_000),
    "plan.md": (375, 35_000), M22: (1_750, 125_000),
    M23: (250, 30_000), M24: (280, 32_000),
}
CANONICAL_DOCS = (
    "AGENTS.md", "README.md", AUTHORITY, "docs/specification.md", "docs/design.md",
    "plan.md", EXECUTION_INDEX, M22, M23, M24, HISTORY_INDEX,
)
FIRST_HEADINGS = {
    "AGENTS.md": "# AGENTS.md",
    "README.md": "# task-governance-tool",
    AUTHORITY: "# Repository Authority Index",
    "docs/specification.md": "# task-governance-tool Current Product Specification",
    "docs/design.md": "# task-governance-tool Current Implementation Design",
    "plan.md": "# task-governance-tool Current Decisions And Open Issues",
    EXECUTION_INDEX: "# Conditional Execution Contract Index",
    M22: "# TG-M22 Evidence Ledger Conditional Execution Contract",
    M23: "# TG-M23 Derived Evidence Conditional Execution Contract",
    M24: "# TG-M24 Verification Runner Conditional Execution Contract",
    HISTORY_INDEX: "# Historical Documentation Index",
}
ROUTE_SECTIONS = (
    (AUTHORITY, "## Mandatory Start Set", ("../AGENTS.md",)),
    (AUTHORITY, "## Selective Current Authority", ("specification.md", "design.md", "../plan.md")),
    (
        AUTHORITY, "## Conditional Formal Authority",
        (
            "execution-contracts/tg-m22-evidence-ledger.md",
            "execution-contracts/tg-m23-derived-evidence.md",
            "execution-contracts/tg-m24-verification-runner.md",
        ),
    ),
    (AUTHORITY, "## Non-Authoritative History", ("history/README.md",)),
    (
        EXECUTION_INDEX, "## Indexed Contracts",
        (
            "tg-m22-evidence-ledger.md#tg-m22-conditional-product",
            "tg-m23-derived-evidence.md#tg-m23-derived-evidence",
            "tg-m24-verification-runner.md#tg-m24-verification-runner",
        ),
    ),
)
AGENTS_SECTION_DIGESTS = {
    "## Source Of Truth": "b879088234a7bb2b9807c7ba993a3f3b7d641fdb2371626fcb87924ba702ea7b",
    "## Reread Rule": "52ebf7567263642e65fad840a5eec36daa6e38d67eb922e994446fce5671a834",
    "## Product Contract Routing And Durable Agent Guardrails": "e0cb3e14ea6168afe059875b6eefdaf87a79640ee348c19b86630d1368330dc9",
}
BANNER_DIGESTS = {
    EXECUTION_INDEX: "b436fd6fdbcadc6ca9c48bd98ae58ec39cafb7cc0ffa9ab6480e3475da22e876",
    M22: "75c3657d96001a3412d66004925c9819505858b4fa28a9c895a8bba6c288134a",
    M23: "a9aff0908e6494e06682fa33c9a79f121c3a874c118b30e9369de1fb36d2bc98",
    M24: "46f92f30d7b9aad7f6eb23436662f4a9939fc57167050eaa0bcf4291a7f346e0",
    HISTORY_INDEX: "934f43005b3038f9d24088b1ba26f85b816f6b42285b39ed29334c1ba1001380",
}
MOVED_HEADINGS = {
    "docs/specification.md": (
        "## Completed TG-M20S Task-Decomposition Observation Boundary",
        "## Accepted But Inactive TG-M21.4 Verification Subject Correction",
        "## Accepted But Inactive TG-M22 Evidence Ledger Contract",
    ),
    "docs/design.md": (
        "## Accepted But Inactive TG-M21.4 Verification Subject Design",
        "## Accepted But Inactive TG-M22 Evidence Ledger Design",
        "## Completed TG-M20 Study Boundary",
        "## Completed TG-M20S Successor Observation Boundary",
    ),
    "plan.md": (
        "### M20 Operational-Baseline Boundary",
        "### M20 Recorded Decisions",
        "### TG-M20S Recorded Successor Decision",
        "### TG-M21 Current Verification Receipt Decision",
        "### Current Authority Layout And Approved M21 Sequence",
        "### TG-M21.4 Through TG-M21.4D Corrections",
    ),
}
ROLE_MARKERS = {
    AUTHORITY: ("It is authority routing, not a product contract, execution ledger, or evidence store.",),
    "docs/specification.md": ("This document specifies supported product behavior.",),
    "docs/design.md": ("This document is the current implementation design for the behavior specified",),
    "plan.md": ("It is not the product contract, execution ledger, or evidence store:", "cross-sequence gateways"),
    EXECUTION_INDEX: ("Each indexed file is the sole detailed owner for its named inactive units'",),
    M22: ("This document is the sole detailed owner of the accepted inactive units'",),
    M23: ("This document is the sole detailed owner of the accepted inactive units'",),
    M24: ("This document is the sole detailed owner of the accepted inactive units'",),
}
ROWS_M22 = (
    ("TG-M22.1A / 25", "tg_task_0e1d93d81eb843ab", "accepted TG-M21.4D and completed TG-DOC.1"),
    ("TG-M22.2 / 30", "tg_task_88bfe19eb6cffe2e", "accepted TG-M22.1A"),
    ("TG-M21.5 / 40", "tg_task_e7701fb907020905", "accepted TG-M22.2"),
    ("TG-M22.3 / 50", "tg_task_ae6f52c4f7b25549", "accepted TG-M21.5"),
    ("TG-M22.4 / 60", "tg_task_0a90b4caf566a8fd", "accepted TG-M22.3"),
)
ROWS_M23 = (
    ("TG-M23.1 / 10", "tg_task_722ac8a308a23d1c", "accepted TG-M22.4"),
    ("TG-M23.2 / 20", "tg_task_d5511d2ca7db93dc", "accepted TG-M23.1"),
    ("TG-M23.3 / 30", "tg_task_0ada32d2b4f9759d", "accepted TG-M23.2"),
)
ROWS_M24 = (
    ("TG-M24.1 / 10", "tg_task_29aa63124900ad95", "accepted TG-M23.3"),
    ("TG-M24.2 / 20", "tg_task_fafad7bc62df7576", "accepted TG-M24.1"),
    ("TG-M24.3 / 30", "tg_task_dc015144091f8e60", "accepted TG-M24.2"),
    ("TG-M24.4 / 40", "tg_task_f81f2d126f033a59", "accepted TG-M24.3"),
)
@dataclass(frozen=True)
class SequenceSpec:
    path: str; heading: str
    headers: tuple[str, ...]; rows: tuple[tuple[str, str, str], ...]
    digest: str
SEQUENCES = (
    SequenceSpec(
        M22, "## Approved Inactive Sequence",
        ("Unit/order", "Task", "Dependency", "Bounded outcome and gate"),
        ROWS_M22, "7058aea4345a064ffbfd4607150f432e9c1cf134fc801893aa511ca9e1f8ab81",
    ),
    SequenceSpec(
        M23, "## Sequence Boundary",
        ("Unit/order", "Task", "Dependency"),
        ROWS_M23, "a426139aafd259e2b939db16ab994caabbaaf38e7bc1648669b91f2a4839626d",
    ),
    SequenceSpec(
        M24, "## Sequence Boundary",
        ("Unit/order", "Task", "Dependency", "Purpose, permission boundary, and completion gate"),
        ROWS_M24, "04beac27358122721d9f18190d0c58136bad17fc8968afb6d6be6f6ec0fc250b",
    ),
    SequenceSpec(
        "plan.md", "### Approved TG-M24 Verification Runner Sequence",
        ("Unit/order", "Task", "Dependency", "Purpose, permission boundary, and completion gate"),
        ROWS_M24, "04beac27358122721d9f18190d0c58136bad17fc8968afb6d6be6f6ec0fc250b",
    ),
)
VOLATILE_ID = re.compile(r"\btg_(?:event|handoff|checkpoint|review_request|review_receipt|review_finding|verification_receipt)_[0-9a-f]{16}\b")
LIVE_STATUS = re.compile(r"(?im)^\s*(?:status|current_status|blocked_reason|pause_reason|completed_at|completion_commit_hash)\s*:\s*(?:ready|in_progress|review_pending|blocked|paused|done|null)\s*$")
ANCHOR = re.compile(r'^<a id="([a-z0-9_-]+)"></a>$')
HEADING = re.compile(r"^(#{1,6}) ([^#].*?)$")
DIRECT_LINK = re.compile(r"(?<!!)\[([^\[\]\n]+)\]\(([^()\s]+)\)")
FENCE_INFO = {"", "text", "json", "powershell", "gitignore"}
@dataclass(frozen=True, order=True)
class Issue:
    code: str; subject: str; message: str

    def to_data(self) -> dict[str, str]:
        return {"code": self.code, "subject": self.subject, "message": self.message}
@dataclass(frozen=True)
class Metric:
    path: str; lines: int; bytes: int
    max_lines: int; max_bytes: int

    def to_data(self) -> dict[str, int | str]:
        return self.__dict__.copy()
@dataclass(frozen=True)
class Link:
    line: int; target: str
@dataclass
class Scan:
    lines: list[str]; visible: list[str]
    headings: list[tuple[int, str, int]]; anchors: dict[str, int]
    links: list[Link]; fences: list[tuple[str, int, int]]
    quotes: list[tuple[str, ...]]
@dataclass(frozen=True)
class Result:
    metrics: tuple[Metric, ...]; current_lines: int | None
    current_bytes: int | None; line_reduction_bp: int | None
    byte_reduction_bp: int | None; issues: tuple[Issue, ...]

    @property
    def ok(self) -> bool:
        return not self.issues

    def to_data(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "metrics": [metric.to_data() for metric in self.metrics],
            "mandatory_read_set": {
                "legacy_lines": LEGACY_LINES,
                "legacy_bytes": LEGACY_BYTES,
                "current_lines": self.current_lines,
                "current_bytes": self.current_bytes,
                "line_reduction_basis_points": self.line_reduction_bp,
                "byte_reduction_basis_points": self.byte_reduction_bp,
            },
            "issues": [issue.to_data() for issue in self.issues],
        }
def _digest(lines: list[str] | tuple[str, ...]) -> str:
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()
def _is_link_like(path: Path) -> bool:
    try:
        data = os.lstat(path)
    except OSError:
        return False
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return path.is_symlink() or bool(getattr(data, "st_file_attributes", 0) & reparse)
def _safe_file(root: Path, relative: str) -> Path | None:
    if "\\" in relative or relative.startswith("/"):
        return None
    current = root
    for part in relative.split("/"):
        if part in {"", ".", ".."}:
            return None
        try:
            names = {entry.name for entry in current.iterdir()}
        except OSError:
            return None
        if part not in names:
            return None
        current = current / part
        if _is_link_like(current):
            return None
    try:
        return current if current.is_file() else None
    except OSError:
        return None
def _read(root: Path, relative: str, issues: list[Issue]) -> tuple[bytes, str] | None:
    path = _safe_file(root, relative)
    if path is None:
        issues.append(Issue("document_unavailable", relative, "required regular file is unavailable"))
        return None
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8").replace("\r\n", "\n")
    except (OSError, UnicodeError):
        issues.append(Issue("document_encoding", relative, "document must be strict UTF-8"))
        return None
    if raw.startswith(b"\xef\xbb\xbf") or not raw.endswith(b"\n"):
        issues.append(Issue("document_encoding", relative, "document must be BOM-free UTF-8 with final newline"))
    return raw, text
def _mask_inline(line: str, carry: str = "") -> tuple[str, str]:
    masked = list(line)
    position = 0
    if carry:
        closing = next((match for match in re.finditer(r"`+", line) if match.group(0) == carry), None)
        if closing is None:
            return " " * len(line), carry
        masked[:closing.end()] = " " * closing.end()
        position = closing.end()
    while True:
        start = re.search(r"`+", line[position:])
        if start is None:
            break
        first = position + start.start()
        delimiter = start.group(0)
        search_at = position + start.end()
        end = None
        for candidate in re.finditer(r"`+", line[search_at:]):
            if candidate.group(0) == delimiter:
                end = search_at + candidate.end()
                break
        if end is None:
            masked[first:] = " " * (len(line) - first)
            return "".join(masked), delimiter
        masked[first:end] = " " * (end - first)
        position = end
    return "".join(masked), ""
def _scan(relative: str, text: str, issues: list[Issue]) -> Scan:
    lines = text.splitlines()
    visible = [""] * len(lines)
    headings: list[tuple[int, str, int]] = []
    anchors: dict[str, int] = {}
    links: list[Link] = []
    fences: list[tuple[str, int, int]] = []
    quote_groups: list[list[str]] = []
    fence_start: int | None = None
    fence_info = ""
    inline_carry = ""
    for index, line in enumerate(lines):
        number = index + 1
        if fence_start is not None:
            if line == "```":
                fences.append((fence_info, fence_start, index))
                fence_start = None
            continue
        if line.startswith("```"):
            info = line[3:]
            if info not in FENCE_INFO:
                issues.append(Issue("syntax_fence", relative, f"line {number}: unsupported fence"))
            else:
                fence_start, fence_info = index, info
            continue
        stripped = line.lstrip(" \t")
        if stripped.startswith("```") or stripped.startswith("~~~") or line.startswith("~~~"):
            issues.append(Issue("syntax_fence", relative, f"line {number}: fence must be canonical and column-zero"))
        if line.startswith(">"):
            if not quote_groups or index == 0 or not lines[index - 1].startswith(">"):
                quote_groups.append([])
            quote_groups[-1].append(line)
            payload = line[1:] if not line.startswith("> ") else line[2:]
        else:
            payload = line
        anchor = None if inline_carry else ANCHOR.fullmatch(line)
        if anchor:
            name = anchor.group(1)
            if name in anchors:
                issues.append(Issue("anchor_duplicate", relative, f"line {number}: duplicate explicit anchor"))
            if index == 0 or index + 1 >= len(lines) or lines[index - 1] or lines[index + 1]:
                issues.append(Issue("syntax_anchor", relative, f"line {number}: explicit anchor requires surrounding blank lines"))
            anchors[name] = index
            visible[index] = line
            continue
        if not inline_carry and stripped.startswith("<a ") and not anchor:
            issues.append(Issue("syntax_anchor", relative, f"line {number}: anchor must use the fixed form"))
        indent = len(line) - len(stripped)
        if line[:1].isspace() and (
            stripped.startswith(("#", "<a "))
            or ((line.startswith("\t") or indent >= 4) and "](" in stripped)
        ):
            issues.append(Issue("syntax_layout", relative, f"line {number}: route-bearing syntax must be column-zero"))
        if "\\`" in payload:
            issues.append(Issue("syntax_inline_code", relative, f"line {number}: escaped backticks are unsupported"))
        masked, inline_carry = _mask_inline(payload, inline_carry)
        visible[index] = masked
        heading = HEADING.fullmatch(masked)
        if heading:
            if masked.rstrip().endswith(" #") or re.search(r"\s#+$", masked):
                issues.append(Issue("syntax_heading", relative, f"line {number}: closing heading hashes are unsupported"))
            headings.append((len(heading.group(1)), line, index))
        elif masked.lstrip().startswith("#"):
            issues.append(Issue("syntax_heading", relative, f"line {number}: heading must be column-zero ATX"))
        if re.fullmatch(r"[=-]{3,}\s*", masked):
            issues.append(Issue("syntax_heading", relative, f"line {number}: Setext/horizontal-rule form is unsupported"))
        if re.search(r"\\[\[\]()]", masked):
            issues.append(Issue("syntax_link", relative, f"line {number}: escaped link punctuation is unsupported"))
        if "![" in masked or re.search(r"^\[[^]]+\]:", masked) or re.search(r"\][ \t]*\[", masked):
            issues.append(Issue("syntax_link", relative, f"line {number}: image/reference links are unsupported"))
        if "<!--" in masked or "-->" in masked or "<" in masked:
            issues.append(Issue("syntax_html", relative, f"line {number}: raw HTML/autolinks are unsupported"))
        remainder = list(masked)
        for match in DIRECT_LINK.finditer(masked):
            links.append(Link(index, match.group(2)))
            remainder[match.start():match.end()] = " " * (match.end() - match.start())
        residual = "".join(remainder)
        unsupported_brackets = re.search(r"\[[^\[\]]+\]", residual)
        if "](" in residual or (unsupported_brackets and residual.strip() not in {"[!IMPORTANT]", "[!CAUTION]"}):
            issues.append(Issue("syntax_link", relative, f"line {number}: link must be one-line direct syntax"))
    if fence_start is not None:
        issues.append(Issue("syntax_fence", relative, "fenced code block is not closed"))
    if inline_carry:
        issues.append(Issue("syntax_inline_code", relative, "inline code span is not closed"))
    for index, line in enumerate(visible[:-1]):
        if line.rstrip().endswith("]") and visible[index + 1].lstrip().startswith("("):
            issues.append(Issue("syntax_link", relative, f"line {index + 1}: multiline links are unsupported"))
    return Scan(lines, visible, headings, anchors, links, fences, [tuple(group) for group in quote_groups])
def _section_bounds(scan: Scan, heading: str) -> tuple[int, int] | None:
    matches = [(level, index) for level, line, index in scan.headings if line == heading]
    if len(matches) != 1:
        return None
    level, start = matches[0]
    end = len(scan.lines)
    for next_level, _line, index in scan.headings:
        if index > start and next_level <= level:
            end = index
            break
    return start, end
def _section_digest(scan: Scan, heading: str) -> str | None:
    bounds = _section_bounds(scan, heading)
    return None if bounds is None else _digest(scan.lines[bounds[0]:bounds[1]])
def _expected_registry() -> dict[str, object]:
    return {
        "schema": "taskgov-document-authority-v1",
        "baseline": {"commit": BASELINE_COMMIT, "mandatory_files": 4, "lines": LEGACY_LINES, "bytes": LEGACY_BYTES},
        "budgets": {path: {"lines": limit[0], "bytes": limit[1]} for path, limit in BUDGETS.items()},
        "mandatory_start": ["AGENTS.md", AUTHORITY, "live_task_contract"],
        "current": ["docs/specification.md", "docs/design.md", "plan.md"],
        "conditional": [
            "docs/execution-contracts/tg-m22-evidence-ledger.md",
            "docs/execution-contracts/tg-m23-derived-evidence.md",
            "docs/execution-contracts/tg-m24-verification-runner.md",
        ],
        "history_index": HISTORY_INDEX,
    }
def _ordered_equal(observed: object, expected: object) -> bool:
    if isinstance(expected, dict):
        return isinstance(observed, dict) and list(observed) == list(expected) and all(
            _ordered_equal(observed[key], value) for key, value in expected.items()
        )
    if isinstance(expected, list):
        return isinstance(observed, list) and len(observed) == len(expected) and all(
            _ordered_equal(left, right) for left, right in zip(observed, expected)
        )
    return type(observed) is type(expected) and observed == expected
def _registry(scan: Scan, issues: list[Issue]) -> None:
    bounds = _section_bounds(scan, "## Machine-Readable Registry")
    blocks = [] if bounds is None else [
        block for block in scan.fences if bounds[0] < block[1] < block[2] < bounds[1]
    ]
    parsed: object = None
    duplicate = False
    if len(blocks) == 1 and blocks[0][0] == "json":
        def pairs(values: list[tuple[str, object]]) -> dict[str, object]:
            nonlocal duplicate
            result: dict[str, object] = {}
            for key, value in values:
                duplicate |= key in result
                result[key] = value
            return result
        try:
            parsed = json.loads("\n".join(scan.lines[blocks[0][1] + 1:blocks[0][2]]), object_pairs_hook=pairs)
        except (json.JSONDecodeError, TypeError, ValueError):
            parsed = None
    if duplicate or not _ordered_equal(parsed, _expected_registry()):
        issues.append(Issue("authority_registry", AUTHORITY, "machine registry is not the fixed closed JSON object"))
def _resolve(root: Path, source: str, target: str) -> tuple[str, str] | None:
    if any(token in target for token in ("%", "?", "\\", ":", " ")) or "//" in target:
        return None
    path_text, separator, fragment = target.partition("#")
    if separator and (not fragment or not re.fullmatch(r"[a-z0-9_-]+", fragment)):
        return None
    if path_text and not re.fullmatch(r"[A-Za-z0-9._/-]+", path_text):
        return None
    parts = path_text.split("/") if path_text else []
    current = Path(source).parent
    leading = True
    for part in parts:
        if part == ".." and leading:
            current = current.parent
        elif part in {"", ".", ".."}:
            return None
        else:
            leading = False
            current /= part
    relative = current.as_posix()
    if relative in {"", "."}:
        relative = source
    if relative.startswith("../") or relative == ".." or _safe_file(root, relative) is None:
        return None
    return relative, fragment
def _links_and_routes(root: Path, scans: dict[str, Scan], issues: list[Issue]) -> None:
    for relative, scan in scans.items():
        for link in scan.links:
            resolved = _resolve(root, relative, link.target)
            if resolved is None:
                issues.append(Issue("link_target", relative, f"line {link.line + 1}: local link target is noncanonical or missing"))
                continue
            target_path, fragment = resolved
            if fragment and (target_path not in scans or fragment not in scans[target_path].anchors):
                issues.append(Issue("link_anchor", relative, f"line {link.line + 1}: fragment must name a visible explicit anchor"))
    for relative, heading, expected in ROUTE_SECTIONS:
        scan = scans[relative]
        bounds = _section_bounds(scan, heading)
        observed = () if bounds is None else tuple(
            link.target for link in scan.links if bounds[0] < link.line < bounds[1]
        )
        if observed != expected:
            issues.append(Issue("authority_route", relative, f"{heading} differs from the closed route registry"))
def _cells(line: str) -> tuple[str, ...]:
    return tuple(cell.strip() for cell in line.strip()[1:-1].split("|"))
def _sequence_table(scan: Scan, spec: SequenceSpec) -> tuple[str, ...] | None:
    bounds = _section_bounds(scan, spec.heading)
    if bounds is None:
        return None
    start = next((index for index in range(bounds[0] + 1, bounds[1]) if scan.visible[index].startswith("|")), None)
    if start is None:
        return None
    end = start
    while end < bounds[1] and scan.visible[end].startswith("|"):
        end += 1
    if any(scan.visible[index].lstrip(" \t").startswith("|") for index in range(bounds[0] + 1, bounds[1]) if not start <= index < end):
        return None
    return tuple(scan.lines[start:end])
def _sequences(scans: dict[str, Scan], issues: list[Issue]) -> None:
    tables: dict[tuple[str, str], tuple[str, ...]] = {}
    for spec in SEQUENCES:
        table = _sequence_table(scans[spec.path], spec)
        tables[(spec.path, spec.heading)] = table or ()
        valid = table is not None and len(table) == len(spec.rows) + 2
        if valid:
            valid = _cells(table[0]) == spec.headers and _cells(table[1]) == tuple("---" for _ in spec.headers)
        if valid:
            observed = tuple((_cells(row)[0], _cells(row)[1].strip("`"), _cells(row)[2]) for row in table[2:])
            valid = observed == spec.rows and _digest(table) == spec.digest
        if not valid:
            issues.append(Issue("sequence_contract", spec.path, f"{spec.heading} table differs from the exact accepted sequence"))
    m24 = tables[(SEQUENCES[2].path, SEQUENCES[2].heading)]
    plan = tables[(SEQUENCES[3].path, SEQUENCES[3].heading)]
    if m24 != plan:
        issues.append(Issue("sequence_m24_mirror", "plan.md", "M24 index must equal the canonical conditional table"))
def _roles(scans: dict[str, Scan], issues: list[Issue]) -> None:
    for relative, expected in FIRST_HEADINGS.items():
        observed = scans[relative].headings[0][1] if scans[relative].headings else ""
        if observed != expected:
            issues.append(Issue("document_role", relative, "first heading differs from the fixed document role"))
    for relative, forbidden in MOVED_HEADINGS.items():
        headings = {line for _level, line, _index in scans[relative].headings}
        if headings.intersection(forbidden):
            issues.append(Issue("document_role", relative, "retired authority heading returned to an active owner"))
    for relative, markers in ROLE_MARKERS.items():
        flattened = " ".join("\n".join(scans[relative].visible).split())
        if not all(marker in flattened for marker in markers):
            issues.append(Issue("document_role", relative, "document owner declaration drifted"))
    for relative, scan in scans.items():
        visible = "\n".join(scan.visible)
        if VOLATILE_ID.search(visible) or LIVE_STATUS.search(visible):
            issues.append(Issue("volatile_state", relative, "Git documentation must not mirror live Task evidence or status"))
        lowered = visible.lower()
        if "is the next sequential" in lowered or "is the current sequential" in lowered:
            issues.append(Issue("volatile_state", relative, "volatile current/next execution wording is forbidden"))
    for heading, expected in AGENTS_SECTION_DIGESTS.items():
        if _section_digest(scans["AGENTS.md"], heading) != expected:
            issues.append(Issue("agents_routing", "AGENTS.md", f"{heading} durable routing contract drifted"))
    if _section_digest(scans[AUTHORITY], "## Trigger Routing") != TRIGGER_ROUTING_SHA256:
        issues.append(Issue("authority_route", AUTHORITY, "Trigger Routing differs from the fixed owner registry"))
    readme = " ".join("\n".join(scans["README.md"].lines).split())
    for required in ("fixed canonical routing syntax", "not a general Markdown or CommonMark parser", "docs/authority.md"):
        if required not in readme:
            issues.append(Issue("document_role", "README.md", "documentation-check or authority guidance is incomplete"))
def _banners(scans: dict[str, Scan], issues: list[Issue]) -> None:
    for relative, scan in scans.items():
        expected = BANNER_DIGESTS.get(relative)
        if expected is None:
            if scan.quotes:
                issues.append(Issue("banner_contract", relative, "unexpected blockquote is unsupported"))
        elif len(scan.quotes) != 1 or _digest(scan.quotes[0]) != expected:
            issues.append(Issue("banner_contract", relative, "authority banner differs from the fixed warning"))
def _history(root: Path, index: Scan, issues: list[Issue]) -> None:
    history_root = root / "docs" / "history"
    captures: list[Path] = []
    try:
        candidates = sorted(history_root.rglob("*"))
    except OSError:
        candidates = []
    for path in candidates:
        if _is_link_like(path):
            issues.append(Issue("history_file", path.relative_to(root).as_posix(), "history must not contain links or reparse points"))
            continue
        if path.is_file() and path.suffix.lower() == ".md":
            if path.suffix != ".md":
                issues.append(Issue("history_file", path.relative_to(root).as_posix(), "history extension must be lowercase .md"))
            if path != history_root / "README.md":
                captures.append(path)
    counts = {path.relative_to(root).as_posix(): 0 for path in captures}
    for link in index.links:
        resolved = _resolve(root, HISTORY_INDEX, link.target)
        if resolved is not None and resolved[0] in counts:
            counts[resolved[0]] += 1
    for relative, count in counts.items():
        if count != 1:
            issues.append(Issue("history_index", relative, "historical Markdown must be indexed exactly once"))
        path = root.joinpath(*relative.split("/"))
        try:
            prefix = path.read_bytes()[:2_048].decode("utf-8")
        except (OSError, UnicodeError):
            prefix = ""
        if "NON-AUTHORITATIVE" not in prefix:
            issues.append(Issue("history_banner", relative, "history lacks an early non-authority banner"))
    pre_m22 = root / "docs" / "history" / "v0.11.0" / "pre-m22-completed-execution.md"
    try:
        digest = hashlib.sha256(pre_m22.read_bytes()).hexdigest()
    except OSError:
        digest = ""
    index_text = "\n".join(index.lines)
    if digest != PRE_M22_SHA256 or f"Source commit: `{BASELINE_COMMIT}`" not in index_text or "Capture unit: `TG-DOC.1`" not in index_text:
        issues.append(Issue("history_provenance", "docs/history/v0.11.0/pre-m22-completed-execution.md", "pre-M22 capture provenance or digest drifted"))
def _budgets(raw_docs: dict[str, tuple[bytes, str]], issues: list[Issue]) -> tuple[tuple[Metric, ...], int | None, int | None, int | None, int | None]:
    metrics: list[Metric] = []
    for relative, (max_lines, max_bytes) in BUDGETS.items():
        if relative not in raw_docs:
            continue
        raw, text = raw_docs[relative]
        metric = Metric(relative, len(text.splitlines()), len(raw), max_lines, max_bytes)
        metrics.append(metric)
        if metric.lines > max_lines or metric.bytes > max_bytes:
            issues.append(Issue("document_budget", relative, "document exceeds its blocking line or byte budget"))
    if all(path in raw_docs for path in ("AGENTS.md", AUTHORITY)):
        current_lines = sum(len(raw_docs[path][1].splitlines()) for path in ("AGENTS.md", AUTHORITY))
        current_bytes = sum(len(raw_docs[path][0]) for path in ("AGENTS.md", AUTHORITY))
        line_bp = (LEGACY_LINES - current_lines) * 10_000 // LEGACY_LINES
        byte_bp = (LEGACY_BYTES - current_bytes) * 10_000 // LEGACY_BYTES
        if min(line_bp, byte_bp) < MIN_REDUCTION_BP:
            issues.append(Issue("mandatory_read_set", AUTHORITY, "mandatory documentation reduction is below 90 percent"))
    else:
        current_lines = current_bytes = line_bp = byte_bp = None
    return tuple(metrics), current_lines, current_bytes, line_bp, byte_bp
def check_document_contract(repo_root: str | os.PathLike[str]) -> Result:
    root = Path(repo_root).resolve()
    issues: list[Issue] = []
    raw_docs: dict[str, tuple[bytes, str]] = {}
    scans: dict[str, Scan] = {}
    for relative in CANONICAL_DOCS:
        document = _read(root, relative, issues)
        if document is not None:
            raw_docs[relative] = document
            scans[relative] = _scan(relative, document[1], issues)
    ignore = _read(root, ".ignore", issues)
    if ignore is not None and ignore[1] != "# Non-authoritative history is opt-in for repository searches.\n/docs/history/\n":
        issues.append(Issue("search_policy", ".ignore", "history search exclusion must be the exact bounded rule"))
    release = _read(root, RELEASE_INSTALL, issues)
    if release is not None and release[1].splitlines()[0] != "# Release Candidate And Published Install Record":
        issues.append(Issue("document_role", RELEASE_INSTALL, "release/install owner heading drifted"))
    if set(scans) == set(CANONICAL_DOCS):
        _registry(scans[AUTHORITY], issues)
        _links_and_routes(root, scans, issues)
        _sequences(scans, issues)
        _roles(scans, issues)
        _banners(scans, issues)
        _history(root, scans[HISTORY_INDEX], issues)
    metrics, lines, bytes_, line_bp, byte_bp = _budgets(raw_docs, issues)
    return Result(metrics, lines, bytes_, line_bp, byte_bp, tuple(sorted(set(issues))))
def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check the fixed repository documentation contract offline and read-only.")
    parser.add_argument("--repo", default=str(DEFAULT_REPO_ROOT), help="source repository root")
    parser.add_argument("--json", action="store_true", help="emit deterministic JSON")
    return parser
def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = check_document_contract(args.repo)
    except Exception:
        result = Result((), None, None, None, None, (Issue("checker_internal_error", "document_contract", "document checker could not complete safely"),))
    if args.json:
        print(json.dumps(result.to_data(), ensure_ascii=False, sort_keys=True))
    elif result.ok:
        print(f"document contract: PASS ({len(result.metrics)} budgeted documents)")
    else:
        print(f"document contract: FAIL ({len(result.issues)} issue(s))")
        for issue in result.issues:
            print(f"- {issue.code}: {issue.subject}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
