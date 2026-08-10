"""Offline semantic checks for this repository's documentation authority."""
from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path


sys.dont_write_bytecode = True

DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[1]
AUTHORITY = "docs/authority.md"
HISTORY_INDEX = "docs/history/README.md"
RELEASE_INSTALL = "docs/release-install.md"
EXECUTION_INDEX = "docs/execution-contracts/README.md"
M22 = "docs/execution-contracts/tg-m22-evidence-ledger.md"
M23 = "docs/execution-contracts/tg-m23-derived-evidence.md"
M23_PROCESS = "docs/execution-contracts/tg-m23-process-safety.md"
M24 = "docs/execution-contracts/tg-m24-verification-runner.md"

CANONICAL_DOCS = (
    "AGENTS.md",
    "README.md",
    AUTHORITY,
    "docs/specification.md",
    "docs/design.md",
    "plan.md",
    EXECUTION_INDEX,
    M22,
    M23,
    M23_PROCESS,
    M24,
    HISTORY_INDEX,
)
METRIC_DOCS = CANONICAL_DOCS + (RELEASE_INSTALL,)

# These sections are closed authority edges. Their prose and link order are not
# part of the contract; the required destination set is.
ROUTE_SECTIONS = (
    (AUTHORITY, "## Mandatory Start Set", ("../AGENTS.md",)),
    (
        AUTHORITY,
        "## Selective Current Authority",
        ("specification.md", "design.md", "../plan.md"),
    ),
    (
        AUTHORITY,
        "## Mixed Current And Conditional Execution Authority",
        (
            "execution-contracts/tg-m22-evidence-ledger.md#tg-m22-sequence",
            "execution-contracts/tg-m23-derived-evidence.md#tg-m23-derived-evidence",
            "execution-contracts/tg-m23-process-safety.md#tg-m23-process-safety",
            "execution-contracts/tg-m24-verification-runner.md#tg-m24-verification-runner",
        ),
    ),
    (
        AUTHORITY,
        "## Documentation Governance Sequence",
        ("../plan.md#tg-doc-sequence",),
    ),
    (AUTHORITY, "## Non-Authoritative History", ("history/README.md",)),
    (
        EXECUTION_INDEX,
        "## Indexed Contracts",
        (
            "tg-m22-evidence-ledger.md#tg-m22-sequence",
            "tg-m23-derived-evidence.md#tg-m23-derived-evidence",
            "tg-m23-process-safety.md#tg-m23-process-safety",
            "tg-m24-verification-runner.md#tg-m24-verification-runner",
        ),
    ),
    (
        EXECUTION_INDEX,
        "## Cross-Sequence Documentation Gateway",
        (
            "../../plan.md#tg-doc-sequence",
            "../../plan.md#tg-doc-2",
            "../../plan.md#tg-doc-3",
        ),
    ),
    (
        M23,
        "## Process Safety Route",
        ("tg-m23-process-safety.md#tg-m23-process-safety",),
    ),
    (
        M23_PROCESS,
        "## Parent Route",
        ("tg-m23-derived-evidence.md#tg-m23-1",),
    ),
)

SOURCE_OWNER_RELATIONS = (
    ("agents.md", ("agent behavior", "safety", "workflow")),
    ("docs/specification.md", ("product behavior",)),
    ("docs/design.md", ("implementation structure",)),
    ("plan.md", ("current decisions", "open issues")),
    ("docs/authority.md", ("task contract", "indexed execution detail")),
)
SOURCE_KNOWN_OWNERS = tuple(owner for owner, _topics in SOURCE_OWNER_RELATIONS)
SOURCE_START_RELATIONS = (
    (
        ("start of every task", "start of each task"),
        ("read and follow", "read"),
        ("minimal start set",),
    ),
    (("agents.md",),),
    (("docs/authority.md",),),
    (("live task contract",), ("public cli",)),
)
REREAD_RELATIONS = (
    (
        ("re-read", "reread"),
        ("minimal start set",),
        ("new task",),
        ("milestone",),
        ("execution-unit boundary", "execution unit boundary"),
        ("planning",),
        ("editing",),
        ("verification",),
        ("review",),
    ),
    (
        (
            "implementation-affecting decision",
            "implementation affecting decision",
            "decision that affects implementation",
            "decision affecting implementation",
        ),
        ("docs/authority.md",),
        ("task contract",),
        ("directly coupled",),
        ("implementation",),
        ("tests",),
    ),
    (
        ("full read",),
        ("docs/specification.md",),
        ("docs/design.md",),
        ("plan.md",),
        ("conditional contract",),
        ("authority-layout", "authority layout"),
        ("transition",),
        ("cross-cutting", "cross cutting"),
        ("missing",),
        ("ambiguous",),
        ("conflict",),
    ),
)
TRIGGER_ROUTE_RELATIONS = (
    (("supported product behavior",), ("docs/specification.md",)),
    (("module ownership", "test architecture"), ("docs/design.md",)),
    (("current decision", "open issue"), ("plan.md",)),
    (
        ("tg-m22 unit purpose",),
        ("docs/execution-contracts/tg-m22-evidence-ledger.md#tg-m22-sequence",),
    ),
    (
        ("tg-m23 unit", "core data"),
        ("docs/execution-contracts/tg-m23-derived-evidence.md",),
    ),
    (
        ("tg-m23 windows process", "atomic publication"),
        (
            "docs/execution-contracts/tg-m23-process-safety.md",
            "core owner/router",
        ),
    ),
    (
        ("documentation governance", "tg-doc unit"),
        ("../plan.md#tg-doc-sequence", "plan.md#tg-doc-2", "plan.md#tg-doc-3"),
    ),
    (
        ("tg-m24 unit detail",),
        (
            "accepted-predecessor",
            "current",
            "inactive unit",
            "mixed execution contract",
            "ascii anchor",
        ),
    ),
    (("published artifact", "release identity"), ("docs/release-install.md",)),
    (
        ("live status", "completion history"),
        ("public cli", "live task contract", "no git-document mirror"),
    ),
    (
        ("historical lineage", "retired evidence"),
        ("docs/history/readme.md", "exceptional reason"),
    ),
)
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
    ("TG-M24.1 / 10", "tg_task_29aa63124900ad95", "accepted TG-DOC.2"),
    ("TG-M24.1A / 15", "tg_task_56e212c793a42272", "accepted TG-M24.1"),
    ("TG-M24.2 / 20", "tg_task_fafad7bc62df7576", "accepted TG-M24.1A"),
    ("TG-M24.3 / 30", "tg_task_dc015144091f8e60", "accepted TG-M24.2"),
    ("TG-M24.4 / 40", "tg_task_f81f2d126f033a59", "accepted TG-M24.3"),
)
ROWS_DOC = (
    (
        "TG-DOC.2 / 40",
        "tg_task_bf2aa245019f5c9f",
        "TG-M23-DERIVED-EVIDENCE",
        "accepted TG-M23.3",
        "accepted predecessor; required before TG-M24.1",
    ),
    (
        "TG-DOC.3 / 20",
        "tg_task_99371b8db2d43eb2",
        "TG-DOC-LIFECYCLE",
        "accepted TG-M24.4 and accepted TG-DOC.2",
        "inactive post-M24",
    ),
)

VOLATILE_ID = re.compile(
    r"\btg_(?:event|handoff|checkpoint|review_request|review_receipt|"
    r"review_finding|verification_receipt)_[0-9a-f]{16}\b"
)
LIVE_STATUS_FIELDS = {
    "status",
    "current_status",
    "blocked_reason",
    "pause_reason",
    "completed_at",
    "completion_commit_hash",
}
LIVE_STATUS_KV = re.compile(
    r"(?i)^(?P<field>status|current_status|blocked_reason|pause_reason|"
    r"completed_at|completion_commit_hash)\s*(?::|=)\s*"
    r"(?P<value>\S(?:.*\S)?)\s*$"
)
LIVE_EXECUTION = re.compile(
    r"(?i)\bis\s+the\s+(?:current|next)\s+(?:sequential\s+)?"
    r"(?:task|unit)\b"
)
LIVE_EXECUTION_REVERSE = re.compile(
    r"(?i)\b(?:the\s+)?(?:current|next)\s+(?:sequential\s+)?(?:task|unit)"
    r"\s*(?:is|:|=)\s*TG-[A-Z0-9.]+\b"
)
TASK_ID = re.compile(r"(?i)\bTG-[A-Z0-9.]+\b")
TASK_STATUS_VALUES = {
    "ready",
    "in_progress",
    "review_pending",
    "blocked",
    "paused",
    "done",
}
NONCURRENT_UNITS = tuple(
    row[0].split(" /", 1)[0]
    for rows in (ROWS_M22, ROWS_M23, ROWS_M24, ROWS_DOC)
    for row in rows
)
NONCURRENT_SUBJECTS = tuple(
    sorted(
        set(NONCURRENT_UNITS) | {"TG-M22", "TG-M23", "TG-M24", "TG-DOC"},
        key=lambda value: (-len(value), value),
    )
)
CURRENT_STATUS_CLAIM = re.compile(
    rf"\b(?:{'|'.join(re.escape(value) for value in NONCURRENT_SUBJECTS)})\b"
    r"(?:\s+(?:sequence|unit|units))?\s+(?:is|are)\s+(?:the\s+)?current\b",
    re.IGNORECASE,
)
UNIT_CURRENT_CLAIM = re.compile(
    r"\b(?:(?:an?|the|named)\s+)?(?:(?:tg-[a-z0-9.]+)\s+)?"
    r"(?:execution\s+)?units?\s+(?:is|are)\s+(?:the\s+)?current\b",
    re.IGNORECASE,
)
LIVE_REVIEW_TARGET_FIELDS = {
    "review_target_kind",
    "review_target_value",
    "review_target_base_revision",
    "review_target_generation",
}
LIVE_REVIEW_TARGET_KV = re.compile(
    r"(?i)^(?P<field>review_target_kind|review_target_value|"
    r"review_target_base_revision|review_target_generation)\s*(?::|=)\s*"
    r"(?P<value>\S(?:.*\S)?)\s*$"
)
MARKDOWN_OWNER_PATH = re.compile(
    r"(?i)(?:\.\./)*(?:[a-z0-9_.-]+/)*[a-z0-9_.-]+\.md"
    r"(?:#[a-z0-9_-]+)?"
)
ANCHOR = re.compile(r'^<a id="([a-z0-9_-]+)"></a>$')
ATX_HEADING = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+|$)(.*)$")
DIRECT_LINK = re.compile(
    r"(?<!!)\[([^\[\]\n]+)\]\(\s*"
    r"(?P<target><[^<>\n]+>|[^()\s]+)"
    r"(?:[ \t]+(?:\"[^\"\n]*\"|'[^'\n]*'|\([^()\n]*\)))?"
    r"[ \t]*\)"
)
REFERENCE_DEFINITION = re.compile(
    r"^ {0,3}\[([^\[\]\n]+)\]:[ \t]*"
    r"(?P<target><[^<>\n]+>|\S+)"
    r"(?:[ \t]+(?:\"[^\"\n]*\"|'[^'\n]*'|\([^()\n]*\)))?"
    r"[ \t]*$"
)
REFERENCE_LINK = re.compile(
    r"(?<!!)\[([^\[\]\n]+)\]\[([^\[\]\n]*)\]"
)
SHORTCUT_REFERENCE = re.compile(
    r"(?<!!)\[([^\[\]\n]+)\](?!\s*(?:[\[(]|:))"
)
EXTERNAL_TARGET = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
WINDOWS_DRIVE_TARGET = re.compile(r"^[A-Za-z]:[/\\]")
INLINE_TICKS = re.compile(re.escape(chr(96)) + r"+")
RAW_HTML_BLOCK_TAGS = (
    "address",
    "article",
    "aside",
    "base",
    "basefont",
    "blockquote",
    "body",
    "caption",
    "center",
    "col",
    "colgroup",
    "dd",
    "details",
    "dialog",
    "dir",
    "div",
    "dl",
    "dt",
    "fieldset",
    "figcaption",
    "figure",
    "footer",
    "form",
    "frame",
    "frameset",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "head",
    "header",
    "hr",
    "html",
    "iframe",
    "legend",
    "li",
    "link",
    "main",
    "menu",
    "menuitem",
    "nav",
    "noframes",
    "ol",
    "optgroup",
    "option",
    "p",
    "param",
    "pre",
    "script",
    "search",
    "section",
    "style",
    "summary",
    "table",
    "tbody",
    "td",
    "textarea",
    "tfoot",
    "th",
    "thead",
    "title",
    "tr",
    "track",
    "ul",
)
RAW_HTML_TYPE1_OPEN = re.compile(
    r"^\s*<(pre|script|style|textarea)(?=\s|>|$)", re.IGNORECASE
)
RAW_HTML_TYPE6_OPEN = re.compile(
    r"^\s*</?(" + "|".join(RAW_HTML_BLOCK_TAGS) + r")(?=\s|/?>|$)",
    re.IGNORECASE,
)
HTML_TAG_NAME = r"[A-Za-z][A-Za-z0-9-]*"
HTML_ATTRIBUTE_NAME = r"[A-Za-z_:][A-Za-z0-9_.:-]*"
HTML_UNQUOTED_VALUE = r'''[^\s"'=<>`]+'''
HTML_ATTRIBUTE = (
    rf"(?:\s+{HTML_ATTRIBUTE_NAME}(?:\s*=\s*(?:{HTML_UNQUOTED_VALUE}|"
    rf"'[^']*'|\"[^\"]*\"))?)"
)
RAW_HTML_TYPE7_LINE = re.compile(
    rf"^\s*(?:<{HTML_TAG_NAME}{HTML_ATTRIBUTE}*\s*/?>|"
    rf"</{HTML_TAG_NAME}\s*>)\s*$"
)
INLINE_HTML_TAG = re.compile(
    rf"</?{HTML_TAG_NAME}{HTML_ATTRIBUTE}*\s*/?>", re.IGNORECASE
)
SEMANTIC_EMPHASIS = re.compile(
    r"(?P<mark>\*\*|__|~~|\*)"
    r"(?P<body>[A-Za-z0-9][A-Za-z0-9_.:/+\-]*?)"
    r"(?P=mark)"
)
SEMANTIC_STRONG_SPAN = re.compile(
    r"(?<!\\)(?P<mark>\*\*|__)(?=\S)"
    r"(?P<body>[^\n]*?\S)(?P=mark)"
)
SEMANTIC_UNDERSCORE_EMPHASIS = re.compile(
    r"(?<![A-Za-z0-9_])_(?P<body>[A-Za-z0-9][A-Za-z0-9_.:/+\-]*?)_"
    r"(?![A-Za-z0-9_])"
)
MARKDOWN_CONTAINER_PREFIX = re.compile(
    r"^ {0,3}(?:(?:[-+*]|\d{1,9}[.)])(?:[ \t]+|$)|>[ \t]?)"
)
FENCE_QUOTE_PREFIX = re.compile(r"^ {0,3}>[ \t]?")
FENCE_LIST_PREFIX = re.compile(
    r"^ {0,3}(?:[-+*]|\d{1,9}[.)])(?:[ \t]{1,4}|$)"
)

# The title tokens declare a document's structural role without fixing its
# complete wording. Equivalent titles may add or reorder words.
ROLE_TITLE_TOKENS = {
    "AGENTS.md": ("agents",),
    "README.md": ("task-governance-tool",),
    AUTHORITY: ("authority", "index"),
    "docs/specification.md": ("product", "specification"),
    "docs/design.md": ("implementation", "design"),
    "plan.md": ("decisions", "issues"),
    EXECUTION_INDEX: ("execution", "contract", "index"),
    M22: ("m22", "evidence", "ledger", "accepted", "contract"),
    M23: ("m23", "derived", "evidence", "accepted", "contract"),
    M23_PROCESS: ("m23", "process", "safety", "contract"),
    M24: ("m24", "verification", "runner", "mixed", "contract"),
    HISTORY_INDEX: ("historical", "documentation", "index"),
    RELEASE_INSTALL: ("release", "install", "record"),
}

# Status is read from the first structural role block, not from a wording hash.
# Positive terms must be asserted rather than directly negated.  A separate
# negative relation is required where the owner declares that no unit is
# current; merely mentioning the word ``current`` is not sufficient.
ROLE_BANNER_STATUS = {
    EXECUTION_INDEX: (("mixed", "current", "conditional", "accepted", "inactive"), False),
    M22: (("accepted", "predecessor"), False),
    M23: (("accepted", "predecessor"), True),
    M23_PROCESS: (("delegated", "accepted"), True),
    M24: (("mixed", "current", "accepted", "predecessor", "inactive"), False),
}


@dataclass(frozen=True, order=True)
class Issue:
    code: str
    subject: str
    message: str

    def to_data(self) -> dict[str, str]:
        return {"code": self.code, "subject": self.subject, "message": self.message}


@dataclass(frozen=True)
class Metric:
    path: str
    lines: int
    bytes: int

    def to_data(self) -> dict[str, int | str]:
        return self.__dict__.copy()


@dataclass(frozen=True)
class Link:
    line: int
    target: str
    route_eligible: bool


@dataclass(frozen=True)
class FenceContainer:
    # Each token is either a block quote marker or the exact content-column
    # indentation contributed by a list marker.  A closer must continue this
    # same container path; a fresh quote/list marker is code content.
    tokens: tuple[tuple[str, int], ...]


@dataclass
class Scan:
    lines: list[str]
    visible: list[str]
    semantic: list[str]
    headings: list[tuple[int, str, int]]
    anchors: dict[str, int]
    links: list[Link]
    fences: list[tuple[str, int, int]]
    quotes: list[tuple[str, ...]]


@dataclass(frozen=True)
class Result:
    metrics: tuple[Metric, ...]
    issues: tuple[Issue, ...]

    @property
    def ok(self) -> bool:
        return not self.issues

    def to_data(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "metrics": [metric.to_data() for metric in self.metrics],
            "issues": [issue.to_data() for issue in self.issues],
        }


@dataclass(frozen=True)
class SequenceSpec:
    path: str
    heading: str
    rows: tuple[tuple[str, ...], ...]
    headers: tuple[str, ...]
    identity_columns: int = 3


SEQUENCES = (
    SequenceSpec(
        M22,
        "## Accepted Sequence",
        ROWS_M22,
        ("Unit/order", "Task", "Dependency", "Bounded outcome and gate"),
    ),
    SequenceSpec(
        M23,
        "## Sequence Boundary",
        ROWS_M23,
        ("Unit/order", "Task", "Dependency"),
    ),
    SequenceSpec(
        M24,
        "## Sequence Boundary",
        ROWS_M24,
        (
            "Unit/order",
            "Task",
            "Dependency",
            "Purpose, permission boundary, and completion gate",
        ),
    ),
    SequenceSpec(
        "plan.md",
        "### TG-DOC Documentation Governance Sequence",
        ROWS_DOC,
        (
            "Unit/order",
            "Task",
            "Lane",
            "Dependency",
            "Authority status and successor gate",
        ),
        identity_columns=5,
    ),
)


@dataclass(frozen=True)
class DocumentationUnit:
    unit: str
    anchor: str
    heading_status: str


DOCUMENTATION_UNITS = (
    DocumentationUnit(
        "TG-DOC.2",
        "tg-doc-2",
        "accepted",
    ),
    DocumentationUnit(
        "TG-DOC.3",
        "tg-doc-3",
        "inactive",
    ),
)


def _is_link_like(path: Path) -> bool:
    try:
        data = os.lstat(path)
    except OSError:
        return False
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return path.is_symlink() or bool(
        getattr(data, "st_file_attributes", 0) & reparse
    )


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


def _read(
    root: Path, relative: str, issues: list[Issue]
) -> tuple[bytes, str] | None:
    path = _safe_file(root, relative)
    if path is None:
        issues.append(
            Issue("document_unavailable", relative, "required regular file is unavailable")
        )
        return None
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8").replace("\r\n", "\n")
    except (OSError, UnicodeError):
        issues.append(
            Issue("document_encoding", relative, "document must be strict UTF-8")
        )
        return None
    if raw.startswith(b"\xef\xbb\xbf") or not raw.endswith(b"\n"):
        issues.append(
            Issue(
                "document_encoding",
                relative,
                "document must be BOM-free UTF-8 with final newline",
            )
        )
    return raw, text


def _is_escaped(line: str, position: int) -> bool:
    backslashes = 0
    position -= 1
    while position >= 0 and line[position] == "\\":
        backslashes += 1
        position -= 1
    return backslashes % 2 == 1


def _markup_views(
    line: str, inline_carry: str = "", html_comment: bool = False
) -> tuple[str, str, str, bool]:
    """Return visible and semantic views while preserving source offsets.

    The visible view masks inline code and comments.  The semantic view masks
    comments and inline delimiters but retains inline-code payload, allowing
    structural checks to see identifiers without trusting hidden comments.
    """

    masked = list(line)
    semantic = list(line)
    position = 0
    while position < len(line):
        if html_comment:
            closing = line.find("-->", position)
            if closing < 0:
                masked[position:] = " " * (len(line) - position)
                semantic[position:] = " " * (len(line) - position)
                return "".join(masked), "".join(semantic), inline_carry, True
            end = closing + 3
            masked[position:end] = " " * (end - position)
            semantic[position:end] = " " * (end - position)
            position = end
            html_comment = False
            continue

        if inline_carry:
            closing = next(
                (
                    match
                    for match in INLINE_TICKS.finditer(line, position)
                    if match.group(0) == inline_carry
                    and not _is_escaped(line, match.start())
                ),
                None,
            )
            if closing is None:
                masked[position:] = " " * (len(line) - position)
                return "".join(masked), "".join(semantic), inline_carry, False
            masked[position : closing.end()] = " " * (closing.end() - position)
            semantic[closing.start() : closing.end()] = " " * len(inline_carry)
            position = closing.end()
            inline_carry = ""
            continue

        if line.startswith("<!--", position):
            closing = line.find("-->", position + 4)
            if closing < 0:
                masked[position:] = " " * (len(line) - position)
                semantic[position:] = " " * (len(line) - position)
                return "".join(masked), "".join(semantic), "", True
            end = closing + 3
            masked[position:end] = " " * (end - position)
            semantic[position:end] = " " * (end - position)
            position = end
            continue

        start = INLINE_TICKS.match(line, position)
        if start is None or _is_escaped(line, position):
            position += 1
            continue
        delimiter = start.group(0)
        closing = next(
            (
                match
                for match in INLINE_TICKS.finditer(line, start.end())
                if match.group(0) == delimiter
                and not _is_escaped(line, match.start())
            ),
            None,
        )
        if closing is None:
            masked[position:] = " " * (len(line) - position)
            semantic[position : start.end()] = " " * len(delimiter)
            return "".join(masked), "".join(semantic), delimiter, False
        masked[position : closing.end()] = " " * (closing.end() - position)
        semantic[position : start.end()] = " " * len(delimiter)
        semantic[closing.start() : closing.end()] = " " * len(delimiter)
        position = closing.end()
    return "".join(masked), "".join(semantic), inline_carry, html_comment


def _mask_markup(
    line: str, inline_carry: str = "", html_comment: bool = False
) -> tuple[str, str, bool]:
    """Mask inline code and HTML comments while preserving source offsets."""

    masked, _semantic, inline_carry, html_comment = _markup_views(
        line, inline_carry, html_comment
    )
    return masked, inline_carry, html_comment


def _mask_inline(line: str, carry: str = "") -> tuple[str, str]:
    """Compatibility wrapper for callers that need only inline-code masking."""

    masked, carry, _html_comment = _mask_markup(line, carry, False)
    return masked, carry


def _semantic_prose(text: str, *, include_link_targets: bool = False) -> str:
    """Remove inline framing; optionally retain direct-link destinations."""

    normalized = DIRECT_LINK.sub(
        lambda match: (
            f"{match.group(1)} {_markdown_link_target(match)}"
            if include_link_targets
            else match.group(1)
        ),
        text,
    )
    normalized = REFERENCE_LINK.sub(lambda match: match.group(1), normalized)
    normalized = SHORTCUT_REFERENCE.sub(lambda match: match.group(1), normalized)
    normalized = INLINE_HTML_TAG.sub("", normalized)
    while True:
        replaced = SEMANTIC_STRONG_SPAN.sub(
            lambda match: match.group("body"), normalized
        )
        replaced = SEMANTIC_EMPHASIS.sub(lambda match: match.group("body"), replaced)
        replaced = SEMANTIC_UNDERSCORE_EMPHASIS.sub(
            lambda match: match.group("body"), replaced
        )
        if replaced == normalized:
            return normalized
        normalized = replaced


def _reference_label(text: str) -> str:
    return " ".join(text.split()).casefold()


def _markdown_link_target(match: re.Match[str]) -> str:
    target = match.group("target")
    return target[1:-1] if target.startswith("<") and target.endswith(">") else target


def _fence_opener(line: str) -> tuple[str, str] | None:
    stripped = line.lstrip(" ")
    if len(line) - len(stripped) > 3 or not stripped:
        return None
    marker_char = ""
    for candidate in (chr(96), "~"):
        if stripped.startswith(candidate * 3):
            marker_char = candidate
            break
    if not marker_char:
        return None
    length = 0
    while length < len(stripped) and stripped[length] == marker_char:
        length += 1
    marker = marker_char * length
    return marker, stripped[length:].strip()


def _fence_closes(line: str, marker: str) -> bool:
    stripped = line.lstrip(" ")
    if len(line) - len(stripped) > 3 or not stripped.startswith(marker):
        return False
    marker_char = marker[0]
    length = 0
    while length < len(stripped) and stripped[length] == marker_char:
        length += 1
    return length >= len(marker) and not stripped[length:].strip()


def _fence_opener_with_container(
    line: str,
) -> tuple[str, str, FenceContainer] | None:
    content = line.expandtabs(4)
    tokens: list[tuple[str, int]] = []
    while True:
        quote = FENCE_QUOTE_PREFIX.match(content)
        if quote is not None:
            prefix = content[: quote.end()]
            leading_indent = len(prefix) - len(prefix.lstrip(" "))
            tokens.append(("quote", leading_indent))
            content = content[quote.end() :]
            continue
        item = FENCE_LIST_PREFIX.match(content)
        if item is not None:
            tokens.append(("list", item.end()))
            content = content[item.end() :]
            continue
        break
    residual_indent = len(content) - len(content.lstrip(" "))
    if residual_indent:
        tokens.append(("indent", residual_indent))
    opener = _fence_opener(content)
    if opener is None and residual_indent >= 4:
        # Four or more columns are normally indented code at top level, but
        # can be a fence inside a list whose preceding item established the
        # container on an earlier line.  Treat that ambiguity as a fence and
        # require the same indentation to close, which fails closed.
        opener = _fence_opener(content[residual_indent:])
    if opener is None:
        return None
    marker, info = opener
    return marker, info, FenceContainer(tuple(tokens))


def _fence_container_content(
    line: str, container: FenceContainer
) -> str | None:
    content = line.expandtabs(4)
    for kind, width in container.tokens:
        if kind == "quote":
            if len(content) < width or content[:width] != " " * width:
                return None
            content = content[width:]
            quote = FENCE_QUOTE_PREFIX.match(content)
            if quote is None:
                return None
            content = content[quote.end() :]
            continue
        # A list-contained fence closes from the item's continuation column.
        # Never consume a fresh list marker: inside a fence it is code bytes.
        if len(content) < width or content[:width] != " " * width:
            return None
        content = content[width:]
    return content


def _fence_closes_in_container(
    line: str, marker: str, container: FenceContainer
) -> bool:
    content = _fence_container_content(line, container)
    return content is not None and _fence_closes(content, marker)


def _markdown_container_content(line: str) -> str:
    content = line
    while True:
        prefix = MARKDOWN_CONTAINER_PREFIX.match(content)
        if prefix is None:
            return content
        content = content[prefix.end() :]


def _html_block_step(line: str, active_tag: str) -> tuple[bool, str]:
    if active_tag:
        kind, _separator, detail = active_tag.partition(":")
        if kind in {"type6", "type7"}:
            return True, "" if not line.strip() else active_tag
        if kind == "type1":
            closed = re.search(
                rf"</{re.escape(detail)}\s*>", line, re.IGNORECASE
            )
            return True, "" if closed else active_tag
        marker = {
            "type2": "-->",
            "type3": "?>",
            "type4": ">",
            "type5": "]]>",
        }[kind]
        return True, "" if marker in line else active_tag

    if ANCHOR.fullmatch(line):
        return False, ""
    container_content = _markdown_container_content(line)
    stripped = container_content.lstrip()
    type1 = RAW_HTML_TYPE1_OPEN.match(container_content)
    if type1 is not None:
        tag = type1.group(1).lower()
        closed = re.search(rf"</{re.escape(tag)}\s*>", line, re.IGNORECASE)
        return True, "" if closed else f"type1:{tag}"
    for opener, marker, state in (
        ("<!--", "-->", "type2:"),
        ("<?", "?>", "type3:"),
        ("<![CDATA[", "]]>", "type5:"),
    ):
        if stripped.startswith(opener):
            return True, "" if marker in stripped[len(opener) :] else state
    if re.match(r"^<![A-Z]", stripped):
        return True, "" if ">" in stripped[2:] else "type4:"
    if RAW_HTML_TYPE6_OPEN.match(container_content):
        return True, "type6:"
    if RAW_HTML_TYPE7_LINE.fullmatch(container_content):
        return True, "type7:"
    return False, ""


def _scan(relative: str, text: str, issues: list[Issue]) -> Scan:
    lines = text.splitlines()
    visible = [""] * len(lines)
    semantic = [""] * len(lines)
    headings: list[tuple[int, str, int]] = []
    anchors: dict[str, int] = {}
    links: list[Link] = []
    reference_definitions: dict[str, str] = {}
    reference_uses: list[tuple[int, str, bool]] = []
    fences: list[tuple[str, int, int]] = []
    quote_groups: list[list[str]] = []
    fence_marker = ""
    fence_info = ""
    fence_container = FenceContainer(())
    fence_poisoned = False
    fence_start = -1
    inline_carry = ""
    html_comment = False
    raw_html_tag = ""
    quote_html_tag = ""
    quote_active = False

    for index, line in enumerate(lines):
        if fence_marker:
            if not line.startswith(">"):
                quote_active = False
            container_content = _fence_container_content(line, fence_container)
            if container_content is None:
                # A container-owned fence ended or changed parent.  This
                # bounded scanner does not reparse CommonMark block state, so
                # keep the remainder inert instead of letting a later
                # look-alike closer re-enable authority text.
                if not fence_poisoned:
                    issues.append(
                        Issue(
                            "markdown_structure",
                            relative,
                            f"line {index + 1}: fenced block left its opening "
                            "container without a compatible close",
                        )
                    )
                fence_poisoned = True
            elif not fence_poisoned and _fence_closes(
                container_content, fence_marker
            ):
                fences.append((fence_info, fence_start, index))
                fence_marker = ""
                fence_info = ""
                fence_container = FenceContainer(())
                fence_poisoned = False
                fence_start = -1
            continue

        is_quote = line.startswith(">")
        if is_quote:
            payload = line[2:] if line.startswith("> ") else line[1:]
        else:
            payload = line
            quote_active = False

        if raw_html_tag:
            _inert_html, raw_html_tag = _html_block_step(line, raw_html_tag)
            quote_active = False
            continue
        if is_quote:
            inert_html, quote_html_tag = _html_block_step(payload, quote_html_tag)
        else:
            quote_html_tag = ""
            inert_html, raw_html_tag = _html_block_step(payload, raw_html_tag)
        if inert_html:
            continue

        indented_code = payload.startswith("\t") or payload.startswith("    ")
        if not (html_comment or inline_carry):
            opener = _fence_opener_with_container(line)
            if opener is not None:
                if not is_quote:
                    quote_active = False
                fence_marker, fence_info, fence_container = opener
                fence_poisoned = False
                fence_start = index
                continue

        if indented_code and not (html_comment or inline_carry):
            quote_active = False
            continue

        masked, semantic_view, inline_carry, html_comment = _markup_views(
            payload, inline_carry, html_comment
        )
        if indented_code:
            quote_active = False
            continue

        if is_quote and masked.strip():
            if not quote_active:
                quote_groups.append([])
            quote_groups[-1].append("> " + masked)
            quote_active = True
        elif is_quote:
            quote_active = False

        anchor = None if is_quote else ANCHOR.fullmatch(masked)
        if anchor:
            name = anchor.group(1)
            if name in anchors:
                issues.append(
                    Issue(
                        "anchor_duplicate",
                        relative,
                        f"line {index + 1}: duplicate explicit anchor",
                    )
                )
            anchors[name] = index
            visible[index] = masked
            semantic[index] = semantic_view
            continue

        visible[index] = " " + masked if is_quote else masked
        semantic[index] = " " + semantic_view if is_quote else semantic_view
        heading = None if is_quote else ATX_HEADING.fullmatch(masked)
        if heading:
            level = len(heading.group(1))
            body = re.sub(r"[ \t]+#+[ \t]*$", "", heading.group(2)).strip()
            if body:
                headings.append((level, "#" * level + " " + body, index))

        definition = None if is_quote else REFERENCE_DEFINITION.fullmatch(masked)
        if definition is not None:
            label = _reference_label(definition.group(1))
            target = _markdown_link_target(definition)
            previous = reference_definitions.get(label)
            if previous is not None and previous != target:
                issues.append(
                    Issue(
                        "link_reference",
                        relative,
                        f"line {index + 1}: duplicate reference label is ambiguous",
                    )
                )
            else:
                reference_definitions.setdefault(label, target)

        reference_source = list(masked)
        for match in DIRECT_LINK.finditer(masked):
            if not _is_escaped(masked, match.start()):
                links.append(
                    Link(
                        index,
                        _markdown_link_target(match),
                        not is_quote and "<" not in masked and ">" not in masked,
                    )
                )
                reference_source[match.start() : match.end()] = " " * (
                    match.end() - match.start()
                )

        if definition is None:
            reference_text = "".join(reference_source)
            full_spans: list[tuple[int, int]] = []
            for match in REFERENCE_LINK.finditer(reference_text):
                if _is_escaped(reference_text, match.start()):
                    continue
                label = _reference_label(match.group(2) or match.group(1))
                reference_uses.append(
                    (
                        index,
                        label,
                        False,
                    )
                )
                full_spans.append((match.start(), match.end()))
            shortcut_source = list(reference_text)
            for start, end in full_spans:
                shortcut_source[start:end] = " " * (end - start)
            shortcut_text = "".join(shortcut_source)
            for match in SHORTCUT_REFERENCE.finditer(shortcut_text):
                if not _is_escaped(shortcut_text, match.start()):
                    reference_uses.append(
                        (
                            index,
                            _reference_label(match.group(1)),
                            False,
                        )
                    )

    for line, label, route_eligible in reference_uses:
        target = reference_definitions.get(label)
        if target is not None:
            links.append(Link(line, target, route_eligible))

    return Scan(
        lines,
        visible,
        semantic,
        headings,
        anchors,
        links,
        fences,
        [tuple(group) for group in quote_groups],
    )


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


def _semantic_section_blocks(scan: Scan, heading: str) -> tuple[str, ...] | None:
    bounds = _section_bounds(scan, heading)
    if bounds is None:
        return None
    blocks: list[str] = []
    current: list[str] = []
    for position in range(bounds[0] + 1, bounds[1]):
        semantic = _semantic_prose(
            scan.semantic[position], include_link_targets=True
        )
        if not scan.lines[position].strip():
            if current:
                blocks.append(" ".join(current))
                current = []
            continue
        if not semantic.strip():
            continue
        starts_item = re.match(
            r"^ {0,3}(?:[-+*]|\d{1,9}[.)])(?:[ \t]+|$)", semantic
        )
        if starts_item and current:
            blocks.append(" ".join(current))
            current = []
        current.append(" ".join(semantic.lower().split()))
    if current:
        blocks.append(" ".join(current))
    return tuple(blocks)


def _relations_present(
    blocks: tuple[str, ...] | None,
    relations: tuple[tuple[tuple[str, ...], ...], ...],
) -> bool:
    return blocks is not None and all(
        any(_relation_present(block, relation) for block in blocks)
        for relation in relations
    )


def _relation_occurrence_negated(text: str, start: int, end: int) -> bool:
    if _directly_negated(text, start):
        return True
    clause_start, _clause_end = _semantic_clause_bounds(text, start)
    prefix = text[max(clause_start, start - 96) : start]
    scope_breaks = tuple(re.finditer(r",\s*(?:and|but|yet|then)\b", prefix))
    if scope_breaks:
        prefix = prefix[scope_breaks[-1].end() :]
    if re.search(
        r"\b(?:(?:do|does|did|can|could|should|must|may|will|would)\s+not|"
        r"never|avoid|without)\b[^.;]{0,80}$",
        prefix,
    ):
        return True
    suffix = text[end : end + 56]
    return bool(
        re.match(
            r"\s+(?:(?:is|are|was|were|remains?|becomes?)\s+"
            r"(?:not|never|no\s+longer)\b|"
            r"(?:do|does|did|can|could|should|must|may|will|would)\s+not\b|"
            r"(?:only\s+)?as\s+"
            r"(?:an?\s+)?(?:reference|example|non-authority)\b)",
            suffix,
        )
    )


def _positive_relation_term(text: str, term: str) -> bool:
    matches = tuple(re.finditer(re.escape(term), text))
    return bool(matches) and all(
        not _relation_occurrence_negated(text, match.start(), match.end())
        for match in matches
    )


def _relation_present(
    text: str, relation: tuple[tuple[str, ...], ...]
) -> bool:
    return all(
        any(_positive_relation_term(text, alternative) for alternative in group)
        for group in relation
    )


def _markdown_owner_paths(text: str) -> set[str]:
    return {
        match.group(0).lstrip("./").split("#", 1)[0].lower()
        for match in MARKDOWN_OWNER_PATH.finditer(text)
    }


def _semantic_clause_bounds(text: str, position: int) -> tuple[int, int]:
    path_positions = {
        offset
        for match in MARKDOWN_OWNER_PATH.finditer(text)
        for offset in range(match.start(), match.end())
    }
    separators = [
        index
        for index, character in enumerate(text)
        if character == ";" or (character == "." and index not in path_positions)
    ]
    before = [index for index in separators if index < position]
    after = [index for index in separators if index >= position]
    return (max(before) + 1 if before else 0, min(after) if after else len(text))


def _source_owner_relation_present(
    block: str, owner: str, topics: tuple[str, ...]
) -> bool:
    if _markdown_owner_paths(block) != _markdown_owner_paths(owner):
        return False
    if not _owner_asserted(block, owner, topics):
        return False
    return not any(
        candidate != owner and _owner_asserted(block, candidate, topics)
        for candidate in SOURCE_KNOWN_OWNERS
    )


def _owner_asserted(
    block: str, owner: str, topics: tuple[str, ...] = ()
) -> bool:
    for match in re.finditer(re.escape(owner), block):
        if _relation_occurrence_negated(block, match.start(), match.end()):
            continue
        clause_start, clause_end = _semantic_clause_bounds(block, match.start())
        before = block[clause_start : match.start()]
        after = block[match.end() : clause_end]
        clause = block[clause_start:clause_end]
        if _markdown_owner_paths(clause) != _markdown_owner_paths(owner):
            continue
        if topics and not all(
            _positive_relation_term(clause, topic) for topic in topics
        ):
            continue
        if re.match(
            r"\s+(?:only\s+)?as\s+(?:an?\s+)?"
            r"(?:reference|example|non-authority)\b",
            after,
        ):
            continue
        directed_match = re.search(
            r"\b(?:follow|prefer|read|use|consult|select|route(?:d)?\s+by|"
            r"govern(?:ed)?\s+by)\b[^.;]{0,160}$",
            before,
        )
        directed_to_owner = bool(
            directed_match
            and not _relation_occurrence_negated(
                before, directed_match.start(), directed_match.end()
            )
        )
        owner_assertion = re.search(
            r"^.{0,80}\b(?:owns?|governs?|controls?|"
            r"is\s+(?:the\s+)?(?:owner|authority))\b",
            after,
        )
        if directed_to_owner or owner_assertion:
            return True
    return False


def _expected_registry() -> dict[str, object]:
    return {
        "schema": "taskgov-document-authority-v4",
        "mandatory_start": ["AGENTS.md", AUTHORITY, "live_task_contract"],
        "current": ["docs/specification.md", "docs/design.md", "plan.md"],
        "mixed_execution": [
            {
                "path": M22,
                "route_anchor": "tg-m22-sequence",
                "current_units": [],
                "inactive_units": [],
            },
            {
                "path": M23,
                "route_anchor": "tg-m23-derived-evidence",
                "current_units": [],
                "inactive_units": [],
                "detail_routes": [
                    {
                        "path": M23_PROCESS,
                        "route_anchor": "tg-m23-process-safety",
                        "parent_anchor": "tg-m23-1",
                        "owner_scope": "windows_process_private_temp_atomic_publication",
                    }
                ],
            },
            {
                "path": M24,
                "route_anchor": "tg-m24-verification-runner",
                "current_units": ["TG-M24.2"],
                "inactive_units": ["TG-M24.3", "TG-M24.4"],
            },
        ],
        "documentation_sequence": {
            "path": "plan.md",
            "route_anchor": "tg-doc-sequence",
            "current_units": [],
            "inactive_units": ["TG-DOC.3"],
        },
        "conditional": [],
        "history_index": HISTORY_INDEX,
    }


def _semantic_equal(observed: object, expected: object) -> bool:
    if isinstance(expected, dict):
        return (
            isinstance(observed, dict)
            and set(observed) == set(expected)
            and all(_semantic_equal(observed[key], value) for key, value in expected.items())
        )
    if isinstance(expected, list):
        return (
            isinstance(observed, list)
            and len(observed) == len(expected)
            and all(
                _semantic_equal(left, right)
                for left, right in zip(observed, expected)
            )
        )
    return type(observed) is type(expected) and observed == expected


def _registry(scan: Scan, issues: list[Issue]) -> dict[str, object] | None:
    bounds = _section_bounds(scan, "## Machine-Readable Registry")
    blocks = (
        []
        if bounds is None
        else [
            block
            for block in scan.fences
            if bounds[0] < block[1] < block[2] < bounds[1]
            and block[0].strip() == "json"
        ]
    )
    parsed: object = None
    duplicate = False
    if len(blocks) == 1:

        def pairs(values: list[tuple[str, object]]) -> dict[str, object]:
            nonlocal duplicate
            result: dict[str, object] = {}
            for key, value in values:
                duplicate |= key in result
                result[key] = value
            return result

        try:
            parsed = json.loads(
                "\n".join(scan.lines[blocks[0][1] + 1 : blocks[0][2]]),
                object_pairs_hook=pairs,
            )
        except (json.JSONDecodeError, TypeError, ValueError):
            parsed = None
    expected = _expected_registry()
    if duplicate or not _semantic_equal(parsed, expected):
        issues.append(
            Issue(
                "authority_registry",
                AUTHORITY,
                "machine registry differs from the semantic authority graph",
            )
        )
        return None
    assert isinstance(parsed, dict)
    return parsed


def _resolve(root: Path, source: str, target: str) -> tuple[str, str] | None:
    if any(token in target for token in ("%", "?", "\\", ":", " ")) or "//" in target:
        return None
    path_text, separator, fragment = target.partition("#")
    if separator and (not fragment or not re.fullmatch(r"[a-z0-9_-]+", fragment)):
        return None
    if path_text and not re.fullmatch(r"[A-Za-z0-9._/-]+", path_text):
        return None
    if not path_text:
        relative = source
    else:
        parts = path_text.split("/")
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
    if (
        relative.startswith("../")
        or relative == ".."
        or _safe_file(root, relative) is None
    ):
        return None
    return relative, fragment


def _is_external_target(target: str) -> bool:
    return (
        not WINDOWS_DRIVE_TARGET.match(target)
        and (bool(EXTERNAL_TARGET.match(target)) or target.startswith("//"))
    )


def _heading_slugs(scan: Scan) -> set[str]:
    slugs: set[str] = set()
    counts: dict[str, int] = {}
    for _level, heading, _position in scan.headings:
        title = heading.lstrip("#").strip().lower()
        base = re.sub(r"[^\w\s-]", "", title, flags=re.UNICODE)
        base = re.sub(r"\s+", "-", base).strip("-")
        if not base:
            continue
        ordinal = counts.get(base, 0)
        counts[base] = ordinal + 1
        slugs.add(base if ordinal == 0 else f"{base}-{ordinal}")
    return slugs


def _links_and_routes(
    root: Path, scans: dict[str, Scan], issues: list[Issue]
) -> None:
    heading_slugs = {relative: _heading_slugs(scan) for relative, scan in scans.items()}
    for relative, scan in scans.items():
        for link in scan.links:
            if _is_external_target(link.target):
                continue
            resolved = _resolve(root, relative, link.target)
            if resolved is None:
                issues.append(
                    Issue(
                        "link_target",
                        relative,
                        f"line {link.line + 1}: local link target is unsafe or missing",
                    )
                )
                continue
            target_path, fragment = resolved
            if fragment and (
                target_path not in scans
                or (
                    fragment not in scans[target_path].anchors
                    and fragment not in heading_slugs[target_path]
                )
            ):
                issues.append(
                    Issue(
                        "link_anchor",
                        relative,
                        f"line {link.line + 1}: fragment must name a reachable anchor or heading",
                    )
                )

    for relative, heading, expected in ROUTE_SECTIONS:
        scan = scans[relative]
        bounds = _section_bounds(scan, heading)
        observed = (
            ()
            if bounds is None
            else tuple(
                link.target
                for link in scan.links
                if bounds[0] < link.line < bounds[1] and link.route_eligible
            )
        )
        valid = Counter(observed) == Counter(expected)
        if valid:
            for target in expected:
                resolved = _resolve(root, relative, target)
                if resolved is None:
                    valid = False
                    break
                target_path, fragment = resolved
                if fragment and (
                    target_path not in scans
                    or fragment not in scans[target_path].anchors
                ):
                    valid = False
                    break
        if not valid:
            issues.append(
                Issue(
                    "authority_route",
                    relative,
                    f"{heading} differs from the semantic route set",
                )
            )


def _cells(line: str) -> tuple[str, ...]:
    return tuple(cell.strip() for cell in line.strip()[1:-1].split("|"))


def _section_table_range(scan: Scan, heading: str) -> tuple[int, int] | None:
    bounds = _section_bounds(scan, heading)
    if bounds is None:
        return None
    start = next(
        (
            index
            for index in range(bounds[0] + 1, bounds[1])
            if scan.visible[index].startswith("|")
        ),
        None,
    )
    if start is None:
        return None
    end = start
    while end < bounds[1] and scan.visible[end].startswith("|"):
        end += 1
    return start, end


def _sequence_table(scan: Scan, heading: str) -> tuple[str, ...] | None:
    table_range = _section_table_range(scan, heading)
    if table_range is None:
        return None
    return tuple(scan.lines[table_range[0] : table_range[1]])


def _semantic_table(scan: Scan, heading: str) -> tuple[str, ...] | None:
    table_range = _section_table_range(scan, heading)
    if table_range is None:
        return None
    return tuple(scan.semantic[table_range[0] : table_range[1]])


def _sequences(scans: dict[str, Scan], issues: list[Issue]) -> None:
    for spec in SEQUENCES:
        scan = scans[spec.path]
        table = _sequence_table(scan, spec.heading)
        valid = table is not None and len(table) == len(spec.rows) + 2
        if valid:
            header = _cells(table[0])
            separator = _cells(table[1])
            valid = (
                header == spec.headers
                and len(separator) == len(header)
                and all(re.fullmatch(r":?-{3,}:?", cell) for cell in separator)
            )
        if valid:
            observed: list[tuple[str, ...]] = []
            for row in table[2:]:
                cells = _cells(row)
                if len(cells) != len(header):
                    valid = False
                    break
                observed.append(
                    tuple(
                        cell.strip(chr(96))
                        for cell in cells[: spec.identity_columns]
                    )
                )
            valid = valid and tuple(observed) == spec.rows
        if valid and table is not None:
            structural_rows = table[2:]
            valid = all(
                sum(task_id in row for row in structural_rows) == 1
                for task_id in (row[1] for row in spec.rows)
            )
        if not valid:
            issues.append(
                Issue(
                    "sequence_contract",
                    spec.path,
                    f"{spec.heading} Task identity, order, or dependency drifted",
                )
            )

    m24 = scans[M24]
    required_m24_anchors = (
        "tg-m24-1",
        "tg-m24-1a",
        "tg-m24-2",
        "tg-m24-3",
        "tg-m24-4",
    )
    if any(anchor not in m24.anchors for anchor in required_m24_anchors):
        issues.append(
            Issue(
                "sequence_contract",
                M24,
                "M24 unit anchors are incomplete",
            )
        )
    else:
        positions = tuple(m24.anchors[anchor] for anchor in required_m24_anchors)
        if positions != tuple(sorted(positions)) or len(set(positions)) != len(
            positions
        ):
            issues.append(
                Issue(
                    "sequence_contract",
                    M24,
                    "M24 unit anchors are out of order",
                )
            )


def _bounded_reading_controls(
    scans: dict[str, Scan], issues: list[Issue]
) -> None:
    agents = scans["AGENTS.md"]
    source_blocks = _semantic_section_blocks(agents, "## Source Of Truth")
    reread_blocks = _semantic_section_blocks(agents, "## Reread Rule")
    valid_agents = (
        _relations_present(source_blocks, SOURCE_START_RELATIONS)
        and source_blocks is not None
        and all(
            any(
                _source_owner_relation_present(block, owner, topics)
                for block in source_blocks
            )
            for owner, topics in SOURCE_OWNER_RELATIONS
        )
        and _relations_present(reread_blocks, REREAD_RELATIONS)
    )
    if not valid_agents:
        issues.append(
            Issue(
                "authority_route",
                "AGENTS.md",
                "bounded start, owner selection, or reread relations are incomplete",
            )
        )

    authority = scans[AUTHORITY]
    table = _semantic_table(authority, "## Trigger Routing")
    valid_routes = table is not None and len(table) == len(TRIGGER_ROUTE_RELATIONS) + 2
    rows: tuple[tuple[str, ...], ...] = ()
    if valid_routes:
        header = tuple(cell.lower() for cell in _cells(table[0]))
        separator = _cells(table[1])
        valid_routes = (
            header == ("trigger", "required selective route")
            and len(separator) == 2
            and all(re.fullmatch(r":?-{3,}:?", cell) for cell in separator)
        )
        if valid_routes:
            parsed: list[tuple[str, ...]] = []
            for raw_row in table[2:]:
                cells = tuple(cell.lower() for cell in _cells(raw_row))
                if len(cells) != 2:
                    valid_routes = False
                    break
                parsed.append(cells)
            rows = tuple(parsed)
    if valid_routes:
        relation_counts: Counter[int] = Counter()
        for trigger, route in rows:
            matches = [
                index
                for index, (identity, _owners) in enumerate(TRIGGER_ROUTE_RELATIONS)
                if _relation_present(
                    trigger, tuple((term,) for term in identity)
                )
            ]
            if len(matches) != 1:
                valid_routes = False
                break
            relation_index = matches[0]
            relation_counts[relation_index] += 1
            owners = TRIGGER_ROUTE_RELATIONS[relation_index][1]
            owner_relation = tuple((term,) for term in owners)
            if not _relation_present(route, owner_relation):
                valid_routes = False
                break
            expected_paths = _markdown_owner_paths(" ".join(owners))
            observed_paths = _markdown_owner_paths(route)
            if observed_paths != expected_paths:
                valid_routes = False
                break
        valid_routes = valid_routes and relation_counts == Counter(
            range(len(TRIGGER_ROUTE_RELATIONS))
        )
    if not valid_routes:
        issues.append(
            Issue(
                "authority_route",
                AUTHORITY,
                "trigger-to-owner selective routing is incomplete or ambiguous",
            )
        )


def _registry_routes(
    scans: dict[str, Scan], registry: dict[str, object], issues: list[Issue]
) -> None:
    route_objects: list[dict[str, object]] = []
    mixed = registry["mixed_execution"]
    assert isinstance(mixed, list)
    route_objects.extend(item for item in mixed if isinstance(item, dict))
    documentation = registry["documentation_sequence"]
    assert isinstance(documentation, dict)
    route_objects.append(documentation)

    for route in route_objects:
        path = route["path"]
        anchor = route["route_anchor"]
        if (
            not isinstance(path, str)
            or not isinstance(anchor, str)
            or path not in scans
            or anchor not in scans[path].anchors
        ):
            issues.append(
                Issue(
                    "authority_route",
                    AUTHORITY,
                    "registered owner path or route anchor is unavailable",
                )
            )
        details = route.get("detail_routes", [])
        if isinstance(details, list):
            for detail in details:
                if not isinstance(detail, dict):
                    continue
                detail_path = detail.get("path")
                detail_anchor = detail.get("route_anchor")
                parent_anchor = detail.get("parent_anchor")
                if (
                    not isinstance(detail_path, str)
                    or not isinstance(detail_anchor, str)
                    or detail_path not in scans
                    or detail_anchor not in scans[detail_path].anchors
                    or not isinstance(parent_anchor, str)
                    or not isinstance(path, str)
                    or path not in scans
                    or parent_anchor not in scans[path].anchors
                ):
                    issues.append(
                        Issue(
                            "authority_route",
                            AUTHORITY,
                            "delegated detail route or parent anchor is unavailable",
                        )
                    )

    conditional = registry["conditional"]
    assert isinstance(conditional, list)
    m24_routes = [
        route
        for route in route_objects
        if route.get("path") == M24
        and route.get("route_anchor") == "tg-m24-verification-runner"
    ]
    if conditional or len(m24_routes) != 1:
        issues.append(
            Issue(
                "authority_route",
                AUTHORITY,
                "mixed M24 owner or route anchor is unavailable",
            )
        )


def _anchor_section(scan: Scan, anchor: str) -> tuple[int, int] | None:
    start = scan.anchors.get(anchor)
    if start is None:
        return None
    later = [position for position in scan.anchors.values() if position > start]
    return start, min(later) if later else len(scan.lines)


def _documentation_sequence(scans: dict[str, Scan], issues: list[Issue]) -> None:
    plan = scans["plan.md"]
    required = ("tg-doc-sequence",) + tuple(unit.anchor for unit in DOCUMENTATION_UNITS)
    if any(anchor not in plan.anchors for anchor in required):
        issues.append(
            Issue(
                "sequence_contract",
                "plan.md",
                "documentation sequence anchors are incomplete",
            )
        )
        return
    positions = tuple(plan.anchors[anchor] for anchor in required)
    if positions != tuple(sorted(positions)) or len(set(positions)) != len(positions):
        issues.append(
            Issue(
                "sequence_contract",
                "plan.md",
                "documentation sequence anchors are out of order",
            )
        )

    for unit in DOCUMENTATION_UNITS:
        bounds = _anchor_section(plan, unit.anchor)
        assert bounds is not None
        next_heading = next(
            (
                line
                for _level, line, position in plan.headings
                if bounds[0] < position < bounds[1]
            ),
            "",
        )
        heading_lower = next_heading.lower()
        valid = (
            re.search(rf"\b{re.escape(unit.unit)}\b", next_heading) is not None
            and _positive_status_term(heading_lower, unit.heading_status)
        )
        if not valid:
            issues.append(
                Issue(
                    "sequence_contract",
                    "plan.md",
                    f"{unit.unit} accepted/inactive owner heading drifted",
                )
            )


def _directly_negated(text: str, position: int) -> bool:
    prefix = text[max(0, position - 32) : position].lower()
    return bool(
        re.search(
            r"(?:\b(?:not|no|never)\s+(?:(?:the|an?)\s+)?|"
            r"\bno\s+longer\s+|\b(?:formerly|previously)\s+|\bnon[-\s]*)$",
            prefix,
        )
    )


def _positive_status_term(text: str, term: str) -> bool:
    pattern = r"\bpredecessors?\b" if term == "predecessor" else rf"\b{re.escape(term)}\b"
    matches = tuple(re.finditer(pattern, text))
    return bool(matches) and all(
        not _directly_negated(text, match.start()) for match in matches
    )


def _negative_current_relation(text: str) -> bool:
    return bool(
        re.search(
            r"\bno\s+(?:(?:tg-[a-z0-9.]+\s+)?(?:execution\s+)?)?"
            r"units?\s+(?:is|are)\s+current\b"
            r"|\b(?:(?:tg-[a-z0-9.]+\s+)?(?:execution\s+)?)?units?\s+"
            r"(?:is|are)\s+(?:not|never|no\s+longer)\s+current\b"
            r"|\bthere\s+(?:is|are)\s+no\s+current\s+(?:unit|task)s?\b",
            text,
        )
    )


def _has_current_status_contradiction(text: str) -> bool:
    return any(
        not _directly_negated(text, match.start())
        for match in CURRENT_STATUS_CLAIM.finditer(text)
    )


def _has_positive_unit_current_relation(text: str) -> bool:
    return any(
        not _directly_negated(text, match.start())
        for match in UNIT_CURRENT_CLAIM.finditer(text)
    )


def _has_live_review_target(text: str) -> bool:
    for raw_line in text.splitlines():
        line = _markdown_container_content(raw_line).strip()
        key_value = LIVE_REVIEW_TARGET_KV.fullmatch(line)
        if key_value and _is_live_review_target_value(
            key_value.group("field"), key_value.group("value")
        ):
            return True
        if line.startswith("|") and line.endswith("|"):
            cells = _cells(line)
            if (
                len(cells) >= 2
                and cells[0].strip(chr(96)).lower() in LIVE_REVIEW_TARGET_FIELDS
                and _is_live_review_target_value(cells[0], cells[1])
            ):
                return True
    return False


def _has_live_status(text: str) -> bool:
    for raw_line in text.splitlines():
        line = _markdown_container_content(raw_line).strip()
        key_value = LIVE_STATUS_KV.fullmatch(line)
        if key_value and _is_live_status_value(
            key_value.group("field"), key_value.group("value")
        ):
            return True
        if line.startswith("|") and line.endswith("|"):
            cells = _cells(line)
            if (
                len(cells) >= 2
                and cells[0].strip(chr(96)).lower() in LIVE_STATUS_FIELDS
                and _is_live_status_value(cells[0], cells[1])
            ):
                return True
    return False


def _has_unit_live_state(text: str) -> bool:
    status_values = "|".join(sorted(TASK_STATUS_VALUES))
    task_pattern = r"TG-[A-Z0-9.]+"
    unit_status_patterns = (
        re.compile(
            rf"(?i)\b{task_pattern}\b[^\n]{{0,40}}\b"
            rf"(?:status|current_status)\s*(?::|is|=)\s*"
            rf"(?:{status_values})\b"
        ),
        re.compile(
            rf"(?i)\b(?:the\s+)?(?:status|current\s+status)\s+of\s+"
            rf"{task_pattern}\b\s*(?:is|:|=)\s*(?:{status_values})\b"
        ),
        re.compile(
            rf"(?i)\b{task_pattern}\b\s*(?:is|:|=)\s*"
            rf"(?:{status_values})\b"
        ),
        re.compile(
            rf"(?i)\b{task_pattern}\b\s*(?:is|:|=)\s*"
            rf"(?:the\s+)?(?:current|next)\b"
        ),
    )
    target_pattern = re.compile(
        r"(?i)\b(?P<field>review_target_kind|review_target_value|"
        r"review_target_base_revision|review_target_generation)\s*(?::|=)\s*"
        r"(?P<value>[^\s,;|]+)"
    )
    table_header: tuple[str, ...] | None = None
    table_ready = False
    for raw_line in text.splitlines():
        line = _markdown_container_content(raw_line).strip()
        if any(pattern.search(line) for pattern in unit_status_patterns):
            return True
        for target in target_pattern.finditer(line):
            if TASK_ID.search(line[: target.start()]) and _is_live_review_target_value(
                target.group("field"), target.group("value").rstrip(".")
            ):
                return True
        if line.startswith("|") and line.endswith("|"):
            cells = tuple(
                " ".join(_semantic_prose(cell).lower().split())
                for cell in _cells(line)
            )
            if any(TASK_ID.fullmatch(cell) for cell in cells) and any(
                cell in TASK_STATUS_VALUES for cell in cells
            ):
                return True
            if (
                len(cells) >= 2
                and cells[0] in {"current task", "current unit", "next task", "next unit"}
                and TASK_ID.fullmatch(cells[1])
            ):
                return True
            if table_header is None:
                table_header = cells
                table_ready = False
                continue
            if (
                not table_ready
                and len(cells) == len(table_header)
                and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells)
            ):
                table_ready = True
                continue
            if table_ready and len(cells) == len(table_header):
                task_columns = tuple(
                    index
                    for index, header in enumerate(table_header)
                    if header
                    in {
                        "task",
                        "task id",
                        "task_id",
                        "unit",
                        "unit id",
                        "unit_id",
                    }
                )
                target_columns = tuple(
                    (index, header)
                    for index, header in enumerate(table_header)
                    if header in LIVE_REVIEW_TARGET_FIELDS
                )
                if any(
                    TASK_ID.fullmatch(cells[task_index])
                    and _is_live_review_target_value(
                        field, cells[target_index]
                    )
                    for task_index in task_columns
                    for target_index, field in target_columns
                ):
                    return True
                continue
            table_header = cells
            table_ready = False
            continue
        table_header = None
        table_ready = False
    return False


def _is_live_status_value(field: str, value: str) -> bool:
    normalized_field = field.strip().strip(chr(96)).lower()
    normalized_value = value.strip().strip(chr(96)).strip().lower()
    if normalized_field in {"status", "current_status"}:
        return normalized_value in {
            "ready",
            "in_progress",
            "review_pending",
            "blocked",
            "paused",
            "done",
        }
    if normalized_field in {"blocked_reason", "pause_reason"}:
        return bool(normalized_value) and not bool(
            re.fullmatch(
                r"(?:string|text|null|nullable|required|optional|none|n/a)",
                normalized_value,
            )
        )
    if normalized_field == "completed_at":
        return bool(
            re.fullmatch(
                r"[12][0-9]{3}-[0-9]{2}-[0-9]{2}t"
                r"[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?z",
                normalized_value,
            )
        )
    if normalized_field == "completion_commit_hash":
        return bool(re.fullmatch(r"[0-9a-f]{40,64}", normalized_value))
    return False


def _is_live_review_target_value(field: str, value: str) -> bool:
    normalized_field = field.strip().strip(chr(96)).lower()
    normalized_value = value.strip().strip(chr(96)).strip().lower()
    if normalized_value == "null":
        return True
    schema_descriptor = bool(
        re.fullmatch(
            r"(?:str(?:ing)?|text|integer|number|hash|digest|revision|value|"
            r"kind|optional|required|nullable|none|n/a)"
            r"(?:\s*[|/]\s*(?:str(?:ing)?|text|integer|number|hash|digest|"
            r"revision|value|kind|optional|required|nullable|none|n/a))*",
            normalized_value,
        )
    )
    if schema_descriptor:
        return False
    if normalized_field == "review_target_generation":
        return bool(re.fullmatch(r"[0-9]+", normalized_value))
    if normalized_field == "review_target_kind":
        return bool(normalized_value)
    if normalized_field == "review_target_base_revision":
        return bool(normalized_value)
    if normalized_field == "review_target_value":
        return bool(normalized_value)
    return False


def _normalize_quote_block(lines: tuple[str, ...] | list[str]) -> str:
    payloads = []
    for line in lines:
        payload = line[1:] if line.startswith(">") else line
        if payload.startswith(" "):
            payload = payload[1:]
        payloads.append(payload)
    return " ".join("\n".join(payloads).split()).lower()


def _roles(
    scans: dict[str, Scan],
    registry: dict[str, object] | None,
    issues: list[Issue],
) -> None:
    for relative, scan in scans.items():
        h1 = [(line, position) for level, line, position in scan.headings if level == 1]
        if len(h1) != 1 or not scan.headings or scan.headings[0][0] != 1:
            issues.append(
                Issue(
                    "document_role",
                    relative,
                    "document must have one visible top-level owner heading",
                )
            )
        else:
            title = h1[0][0].lower()
            required_title = ROLE_TITLE_TOKENS.get(relative, ())
            if not all(
                _positive_status_term(title, token) for token in required_title
            ):
                issues.append(
                    Issue(
                        "document_role",
                        relative,
                        "top-level heading contradicts the registered document role",
                    )
                )

        required_banner = ROLE_BANNER_STATUS.get(relative)
        if required_banner is not None:
            banner = (
                ""
                if not scan.quotes
                else _normalize_quote_block(scan.quotes[0])
            )
            positive_terms, requires_no_current = required_banner
            valid_banner = all(
                _positive_status_term(banner, term) for term in positive_terms
            ) and (
                not requires_no_current or _negative_current_relation(banner)
            ) and not _has_positive_unit_current_relation(banner)
            if not valid_banner:
                issues.append(
                    Issue(
                        "document_role",
                        relative,
                        "first structural role block does not assert the registered authority status",
                    )
                )

        semantic = "\n".join(scan.semantic)
        semantic_prose = _semantic_prose(semantic)
        normalized_semantic = " ".join(semantic_prose.split())
        if _has_current_status_contradiction(normalized_semantic):
            issues.append(
                Issue(
                    "document_role",
                    relative,
                    "prose contradicts the registered authority status",
                )
            )
        if (
            VOLATILE_ID.search(semantic_prose)
            or _has_live_status(semantic_prose)
            or LIVE_EXECUTION.search(semantic_prose)
            or LIVE_EXECUTION_REVERSE.search(semantic_prose)
            or _has_live_review_target(semantic_prose)
            or _has_unit_live_state(semantic_prose)
        ):
            issues.append(
                Issue(
                    "volatile_state",
                    relative,
                    "Git documentation must not mirror live Task evidence or status",
                )
            )

    if registry is None:
        return
    current = registry["current"]
    mixed = registry["mixed_execution"]
    conditional = registry["conditional"]
    history = registry["history_index"]
    assert isinstance(current, list)
    assert isinstance(mixed, list)
    assert isinstance(conditional, list)
    assert isinstance(history, str)
    role_paths = list(current)
    role_paths.extend(
        item["path"] for item in mixed if isinstance(item, dict) and "path" in item
    )
    role_paths.extend(conditional)
    role_paths.append(history)
    if (
        len(role_paths) != len(set(role_paths))
        or history in current
        or history in conditional
        or any(
            isinstance(item, dict) and item.get("path") == history for item in mixed
        )
    ):
        issues.append(
            Issue(
                "document_role",
                AUTHORITY,
                "current, execution, conditional, and history owners overlap",
            )
        )


def _valid_history_declaration(block: str, *, index: bool) -> bool:
    lowered = _normalize_quote_block(block.splitlines())
    declared = _positive_status_term(lowered, "non-authoritative") or bool(
        index
        and re.search(r"\bnot\s+(?:the\s+)?current\s+authority\b", lowered)
    )
    conflicting = False
    for match in re.finditer(r"\bauthoritative\b", lowered):
        if not _directly_negated(lowered, match.start()):
            conflicting = True
            break
    for match in re.finditer(r"\b(?:binding|current|active)\s+authority\b", lowered):
        if _directly_negated(lowered, match.start()):
            continue
        relation = match.group(0)
        if relation == "current authority":
            suffix = lowered[match.end() : match.end() + 48]
            prefix = lowered[max(0, match.start() - 8) : match.start()]
            routed_replacement = bool(
                re.match(r"\s+(?:is|remains)\s+(?:in\s+)?\[", suffix)
                or prefix.endswith("for ")
            )
            if routed_replacement:
                continue
        conflicting = True
        break
    return declared and not conflicting


def _visible_quote_warning(
    lines: list[str], position: int, *, index: bool
) -> bool:
    block: list[str] = []
    inline_carry = ""
    html_comment = False
    fence_marker = ""
    fence_container = FenceContainer(())
    fence_poisoned = False
    raw_html_tag = ""
    while position < len(lines) and lines[position].startswith(">"):
        line = lines[position]
        payload = line[2:] if line.startswith("> ") else line[1:]
        if fence_marker:
            container_content = _fence_container_content(line, fence_container)
            if container_content is None:
                fence_poisoned = True
            elif not fence_poisoned and _fence_closes(
                container_content, fence_marker
            ):
                fence_marker = ""
                fence_container = FenceContainer(())
                fence_poisoned = False
            position += 1
            continue
        inert_html, raw_html_tag = _html_block_step(payload, raw_html_tag)
        if inert_html:
            position += 1
            continue
        indented_code = payload.startswith("\t") or payload.startswith("    ")
        if not (html_comment or inline_carry):
            opener = _fence_opener_with_container(line)
            if opener is not None:
                fence_marker, _info, fence_container = opener
                fence_poisoned = False
                position += 1
                continue
        masked, inline_carry, html_comment = _mask_markup(
            payload, inline_carry, html_comment
        )
        if not indented_code:
            block.append("> " + masked)
        position += 1
    return bool(block) and _valid_history_declaration("\n".join(block), index=index)


def _first_structural_warning(text: str, *, index: bool = False) -> bool:
    lines = text.replace("\r\n", "\n").splitlines()
    position = 0
    while position < len(lines) and not lines[position].strip():
        position += 1
    if position >= len(lines):
        return False
    first = lines[position].lstrip("\ufeff")
    masked_first, _carry, _comment = _mask_markup(first)
    heading = ATX_HEADING.fullmatch(masked_first)
    if heading and len(heading.group(1)) == 1:
        body = re.sub(r"[ \t]+#+[ \t]*$", "", heading.group(2)).strip()
        if _valid_history_declaration(body, index=index):
            return True
        position += 1
        while position < len(lines) and not lines[position].strip():
            position += 1
    if position >= len(lines) or not lines[position].startswith(">"):
        return False
    return _visible_quote_warning(lines, position, index=index)


def _history(root: Path, index: Scan, issues: list[Issue]) -> None:
    history_root = root / "docs" / "history"
    captures: list[Path] = []
    try:
        candidates = sorted(history_root.rglob("*"))
    except OSError:
        candidates = []
        issues.append(
            Issue(
                "history_file",
                "docs/history",
                "history directory could not be enumerated safely",
            )
        )
    for path in candidates:
        relative = path.relative_to(root).as_posix()
        if _is_link_like(path):
            issues.append(
                Issue(
                    "history_file",
                    relative,
                    "history must not contain links or reparse points",
                )
            )
            continue
        if path.is_file():
            if path == history_root / "README.md":
                continue
            if path.suffix != ".md":
                issues.append(
                    Issue(
                        "history_file",
                        relative,
                        "history regular files must use lowercase .md",
                    )
                )
                continue
            captures.append(path)

    counts = {path.relative_to(root).as_posix(): 0 for path in captures}
    for link in index.links:
        resolved = _resolve(root, HISTORY_INDEX, link.target)
        if resolved is not None and resolved[0] in counts:
            counts[resolved[0]] += 1

    for relative, count in counts.items():
        if count != 1:
            issues.append(
                Issue(
                    "history_index",
                    relative,
                    "historical Markdown must be indexed exactly once",
                )
            )
        path = root.joinpath(*relative.split("/"))
        try:
            text = path.read_bytes().decode("utf-8")
        except (OSError, UnicodeError):
            issues.append(
                Issue(
                    "history_file",
                    relative,
                    "historical Markdown must be readable UTF-8",
                )
            )
            continue
        if not _first_structural_warning(text):
            issues.append(
                Issue(
                    "history_banner",
                    relative,
                    "first structural role block must declare non-authoritative history",
                )
            )

    if not _first_structural_warning("\n".join(index.lines) + "\n", index=True):
        issues.append(
            Issue(
                "history_banner",
                HISTORY_INDEX,
                "history index must begin with a non-authority warning",
            )
        )


def _ignore_glob_regex(rule: str) -> re.Pattern[str]:
    anchored = rule.startswith("/") or "/" in rule
    pattern = rule.lstrip("/")
    directory_only = pattern.endswith("/")
    if directory_only:
        pattern = pattern[:-1]

    translated: list[str] = []
    position = 0
    while position < len(pattern):
        character = pattern[position]
        if character == "*":
            if position + 1 < len(pattern) and pattern[position + 1] == "*":
                position += 2
                if position < len(pattern) and pattern[position] == "/":
                    translated.append("(?:.*/)?")
                    position += 1
                else:
                    translated.append(".*")
                continue
            translated.append("[^/]*")
        elif character == "?":
            translated.append("[^/]")
        elif character == "[":
            end = position + 1
            if end < len(pattern) and pattern[end] in ("!", "^"):
                end += 1
            if end < len(pattern) and pattern[end] == "]":
                end += 1
            while end < len(pattern) and pattern[end] != "]":
                end += 1
            if end >= len(pattern):
                translated.append(r"\[")
            else:
                content = pattern[position + 1 : end]
                if content.startswith("!"):
                    content = "^" + content[1:]
                elif content.startswith("^"):
                    content = "\\" + content
                translated.append("[" + content.replace("\\", r"\\") + "]")
                position = end
        else:
            translated.append(re.escape(character))
        position += 1

    prefix = "^" if anchored else r"^(?:.*/)?"
    suffix = r"(?:/.*)?$" if directory_only else "$"
    return re.compile(prefix + "".join(translated) + suffix)


def _ignore_rule_matches(path: str, rule: str) -> bool:
    return bool(_ignore_glob_regex(rule).fullmatch(path))


def _gitignore_consumer_rule(rule: str) -> str:
    while rule.endswith(" "):
        backslashes = 0
        position = len(rule) - 2
        while position >= 0 and rule[position] == "\\":
            backslashes += 1
            position -= 1
        if backslashes % 2:
            break
        rule = rule[:-1]
    return rule


def _search_policy(root: Path, text: str, issues: list[Issue]) -> None:
    rules = [
        line
        for line in text.splitlines()
        if line and not line.startswith("#")
    ]
    history_root = root / "docs" / "history"
    try:
        candidates = {"docs/history"}
        candidates.update(
            path.relative_to(root).as_posix() for path in history_root.rglob("*")
        )
    except OSError:
        issues.append(
            Issue(
                "search_policy",
                ".ignore",
                "history search exclusion could not be inspected safely",
            )
        )
        return

    exact_seen = False
    effective = False
    for rule in rules:
        if rule == "/docs/history/":
            exact_seen = True
            effective = True
            continue
        consumer_rule = _gitignore_consumer_rule(rule)
        if not exact_seen or not consumer_rule.startswith("!"):
            continue
        pattern = consumer_rule[1:]
        if any(_ignore_rule_matches(candidate, pattern) for candidate in candidates):
            effective = False
    if not exact_seen or not effective:
        issues.append(
            Issue(
                "search_policy",
                ".ignore",
                "ordinary repository search must exclude docs/history",
            )
        )


def _metrics(raw_docs: dict[str, tuple[bytes, str]]) -> tuple[Metric, ...]:
    return tuple(
        Metric(relative, len(raw_docs[relative][1].splitlines()), len(raw_docs[relative][0]))
        for relative in METRIC_DOCS
        if relative in raw_docs
    )


def check_document_contract(repo_root: str | os.PathLike[str]) -> Result:
    root = Path(repo_root).resolve()
    issues: list[Issue] = []
    raw_docs: dict[str, tuple[bytes, str]] = {}
    scans: dict[str, Scan] = {}

    for relative in METRIC_DOCS:
        document = _read(root, relative, issues)
        if document is not None:
            raw_docs[relative] = document
            scans[relative] = _scan(relative, document[1], issues)

    ignore = _read(root, ".ignore", issues)
    if ignore is not None:
        _search_policy(root, ignore[1], issues)

    registry: dict[str, object] | None = None
    if all(relative in scans for relative in CANONICAL_DOCS):
        registry = _registry(scans[AUTHORITY], issues)
        _links_and_routes(root, scans, issues)
        _sequences(scans, issues)
        _bounded_reading_controls(scans, issues)
        _documentation_sequence(scans, issues)
        if registry is not None:
            _registry_routes(scans, registry, issues)
        _history(root, scans[HISTORY_INDEX], issues)

    _roles(scans, registry, issues)
    return Result(_metrics(raw_docs), tuple(sorted(set(issues))))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check repository documentation authority offline and read-only."
    )
    parser.add_argument(
        "--repo", default=str(DEFAULT_REPO_ROOT), help="source repository root"
    )
    parser.add_argument("--json", action="store_true", help="emit deterministic JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = check_document_contract(args.repo)
    except Exception:
        result = Result(
            (),
            (
                Issue(
                    "checker_internal_error",
                    "document_contract",
                    "document checker could not complete safely",
                ),
            ),
        )
    if args.json:
        print(json.dumps(result.to_data(), ensure_ascii=False, sort_keys=True))
    elif result.ok:
        print(f"document contract: PASS ({len(result.metrics)} documents measured)")
    else:
        print(f"document contract: FAIL ({len(result.issues)} issue(s))")
        for issue in result.issues:
            print(f"- {issue.code}: {issue.subject}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
