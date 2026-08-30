"""Offline structural checks for this repository's documentation authority."""
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
DESIGN = "docs/design.md"
HISTORY_INDEX = "docs/history/README.md"
RELEASE_INSTALL = "docs/release-install.md"

CANONICAL_DOCS = (
    "AGENTS.md",
    "README.md",
    AUTHORITY,
    "docs/specification.md",
    DESIGN,
    "plan.md",
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
    (AUTHORITY, "## Non-Authoritative History", ("history/README.md",)),
)

TRIGGER_ROUTE_OWNER_TOKENS = (
    ("docs/specification.md",),
    ("docs/design.md",),
    ("plan.md",),
    ("docs/release-install.md",),
    (),
    ("docs/history/README.md",),
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
INLINE_CODE_TOKEN = re.compile(r"(?<!`)`([^`\r\n]+)`(?!`)")
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

ROLE_TITLES = {
    "AGENTS.md": "# AGENTS.md",
    "README.md": "# task-governance-tool",
    AUTHORITY: "# Repository Authority Index",
    "docs/specification.md": "# task-governance-tool Current Product Specification",
    "docs/design.md": "# task-governance-tool Current Implementation Design",
    "plan.md": "# task-governance-tool Current Decisions And Open Issues",
    HISTORY_INDEX: "# Historical Documentation Index",
    RELEASE_INSTALL: "# Release Candidate And Published Install Record",
}
HISTORY_MARKERS = (
    "NON-AUTHORITATIVE HISTORY",
    "NON-AUTHORITATIVE STUDY HISTORY",
)


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


def _expected_registry() -> dict[str, object]:
    return {
        "schema": "taskgov-document-authority-v6",
        "mandatory_start": ["AGENTS.md", AUTHORITY, "live_task_contract"],
        "current": ["docs/specification.md", "docs/design.md", "plan.md"],
        "mixed_execution": [],
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
                "machine registry differs from the closed authority graph",
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
                    f"{heading} differs from the closed route set",
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


def _structural_reading_controls(
    scans: dict[str, Scan], issues: list[Issue]
) -> None:
    agents = scans["AGENTS.md"]
    if any(
        _section_bounds(agents, heading) is None
        for heading in ("## Source Of Truth", "## Reread Rule")
    ):
        issues.append(
            Issue(
                "authority_route",
                "AGENTS.md",
                "required start and reread sections are missing or duplicated",
            )
        )

    authority = scans[AUTHORITY]
    table_range = _section_table_range(authority, "## Trigger Routing")
    valid_routes = (
        table_range is not None
        and table_range[1] - table_range[0]
        == len(TRIGGER_ROUTE_OWNER_TOKENS) + 2
    )
    if valid_routes and table_range is not None:
        start, end = table_range
        header = tuple(cell.lower() for cell in _cells(authority.semantic[start]))
        separator = _cells(authority.semantic[start + 1])
        valid_routes = (
            header == ("trigger", "required selective route")
            and len(separator) == 2
            and all(re.fullmatch(r":?-{3,}:?", cell) for cell in separator)
        )
        if valid_routes:
            for position, expected_tokens in zip(
                range(start + 2, end), TRIGGER_ROUTE_OWNER_TOKENS
            ):
                semantic_cells = _cells(authority.semantic[position])
                raw_cells = _cells(authority.lines[position])
                if (
                    len(semantic_cells) != 2
                    or len(raw_cells) != 2
                    or not semantic_cells[0].strip()
                    or not semantic_cells[1].strip()
                ):
                    valid_routes = False
                    break
                semantic_route = semantic_cells[1]
                observed_tokens = tuple(
                    match.group(1)
                    for match in INLINE_CODE_TOKEN.finditer(raw_cells[1])
                    if match.group(1) in semantic_route
                )
                if observed_tokens != expected_tokens:
                    valid_routes = False
                    break
    if not valid_routes:
        issues.append(
            Issue(
                "authority_route",
                AUTHORITY,
                "trigger-route table structure or ordered owner tokens differ",
            )
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
            if h1[0][0] != ROLE_TITLES[relative]:
                issues.append(
                    Issue(
                        "document_role",
                        relative,
                        "top-level heading differs from the closed document role",
                    )
                )

        semantic = "\n".join(scan.semantic)
        semantic_prose = _semantic_prose(semantic)
        if (
            VOLATILE_ID.search(semantic_prose)
            or _has_live_status(semantic_prose)
            or _has_live_review_target(semantic_prose)
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
                "registered current, conditional, and history owners overlap",
            )
        )


def _starts_with_history_marker(text: str) -> bool:
    for marker in HISTORY_MARKERS:
        if text == marker:
            return True
        if text.startswith(marker):
            suffix = text[len(marker) :]
            if suffix and not (suffix[0].isalnum() or suffix[0] == "_"):
                return True
    return False


def _contains_history_marker(text: str) -> bool:
    for marker in HISTORY_MARKERS:
        position = text.find(marker)
        while position >= 0:
            end = position + len(marker)
            before_ok = position == 0 or not (
                text[position - 1].isalnum() or text[position - 1] == "_"
            )
            after_ok = end == len(text) or not (
                text[end].isalnum() or text[end] == "_"
            )
            if before_ok and after_ok:
                return True
            position = text.find(marker, position + 1)
    return False


def _has_exact_history_marker(block: list[str]) -> bool:
    payloads: list[str] = []
    for line in block:
        payload = line[1:] if line.startswith(">") else line
        if payload.startswith(" "):
            payload = payload[1:]
        payloads.append(payload.strip())
    visible_text = " ".join(_semantic_prose(" ".join(payloads)).split())
    return _contains_history_marker(visible_text)


def _visible_quote_history_marker(lines: list[str], position: int) -> bool:
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
    return _has_exact_history_marker(block)


def _first_structural_history_marker(text: str) -> bool:
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
        if _starts_with_history_marker(_semantic_prose(body)):
            return True
        position += 1
        while position < len(lines) and not lines[position].strip():
            position += 1
    if position >= len(lines) or not lines[position].startswith(">"):
        return False
    return _visible_quote_history_marker(lines, position)


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
        if not _first_structural_history_marker(text):
            issues.append(
                Issue(
                    "history_banner",
                    relative,
                    "first structural role block must declare non-authoritative history",
                )
            )

    if not _first_structural_history_marker("\n".join(index.lines) + "\n"):
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
        _structural_reading_controls(scans, issues)
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
