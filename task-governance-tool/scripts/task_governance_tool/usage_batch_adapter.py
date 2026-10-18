"""Closed, non-evaluating projection of the observed indexed batch transport.

Only positional tool kinds and literal poll handles leave the input parser.
Command text, arguments, store keys and rejected-result bodies are never state.
"""

from dataclasses import dataclass
import json
import re

from task_governance_tool.usage_values import UsageError


MAX_TEXT = 2_097_152
MAX_SLOTS = 128
_KEY = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")
_NUMBER = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")
_PRAGMA = re.compile(r"\A\s*// @exec: \{[^\r\n]{0,200}\}\r?\n")
_EXEC_FIELDS = {"cmd", "workdir", "shell", "login", "tty", "yield_time_ms",
                "max_output_tokens", "sandbox_permissions", "justification", "prefix_rule"}
_CONTAINER_FIELDS = {"output", "content", "chunk_id", "wall_time_seconds", "session_id",
                     "exit_code", "original_token_count", "isError", "structuredContent", "_meta"}


@dataclass(frozen=True)
class BatchSlot:
    kind: str
    handle: tuple[str, str] | None = None


@dataclass(frozen=True)
class BatchResult:
    """Ephemeral value plus the transport depth already consumed outside it."""

    value: object
    depth: int


class BatchOutput(dict):
    """Ephemeral routing with presence metadata for unproven outer calls."""

    def __init__(self):
        super().__init__()
        self.indexed = False


class AmbiguousBatch(ValueError):
    """A structural ambiguity invalidates every slot in the result container."""


class _LiteralReader:
    """A small JSON-value grammar with static unquoted object keys."""

    def __init__(self, text):
        self.text, self.offset = text, 0

    def take(self, token):
        self.space()
        if not self.text.startswith(token, self.offset):
            raise ValueError
        self.offset += len(token)
        if (token[-1:].isalnum() and self.offset < len(self.text)
                and (self.text[self.offset].isalnum() or self.text[self.offset] in "_$")):
            raise ValueError

    def space(self):
        while self.offset < len(self.text) and self.text[self.offset] in " \t\r\n":
            self.offset += 1

    def peek(self):
        self.space()
        return self.text[self.offset:self.offset + 1]

    def string(self):
        self.space()
        if self.peek() != '"':
            raise ValueError
        value, end = json.JSONDecoder().raw_decode(self.text, self.offset)
        if not isinstance(value, str):
            raise ValueError
        self.offset = end
        return value

    def value(self, depth=0):
        if depth > 8:
            raise ValueError
        next_char = self.peek()
        if next_char == '"':
            return self.string()
        if next_char == "{":
            self.take("{")
            result = {}
            if self.peek() == "}":
                self.take("}")
                return result
            while True:
                if self.peek() == '"':
                    key = self.string()
                else:
                    match = _KEY.match(self.text, self.offset)
                    if match is None:
                        raise ValueError
                    key, self.offset = match[0], match.end()
                if key in result:
                    raise ValueError
                self.take(":")
                result[key] = self.value(depth + 1)
                if self.peek() == "}":
                    self.take("}")
                    return result
                self.take(",")
        if next_char == "[":
            self.take("[")
            result = []
            if self.peek() == "]":
                self.take("]")
                return result
            while True:
                result.append(self.value(depth + 1))
                if self.peek() == "]":
                    self.take("]")
                    return result
                self.take(",")
        for token, value in (("true", True), ("false", False), ("null", None)):
            if self.text.startswith(token, self.offset):
                self.offset += len(token)
                return value
        match = _NUMBER.match(self.text, self.offset)
        if match is None:
            raise ValueError
        self.offset = match.end()
        return json.loads(match[0])


def _code_tokens(text):
    """Bounded lexical inspection, never execution or general JS validation."""
    offset, previous = 0, None
    # These punctuation tokens cannot end an expression. Names and +/- are
    # deliberately excluded: a property keyword or postfix update can precede
    # division. Uncertain slash contexts are unknown, never skipped as text.
    regex_prefixes = {"(", "[", "{", "=", ",", ";", ":", "!", "?", "~", "*", "%", "&", "|", "^"}
    line_ends = "\r\n\u2028\u2029"
    while offset < len(text):
        char = text[offset]
        if char.isspace():
            offset += 1
            continue
        if text.startswith("//", offset):
            end = offset + 2
            while end < len(text) and text[end] not in line_ends:
                end += 1
            offset = end
            continue
        if text.startswith("/*", offset):
            end = text.find("*/", offset + 2)
            offset = len(text) if end < 0 else end + 2
            continue
        if char in {'"', "'", "`"}:
            quote, dynamic = char, False
            offset += 1
            while offset < len(text):
                if text[offset] == "\\":
                    offset += 2
                    continue
                if quote == "`" and text.startswith("${", offset):
                    dynamic = True
                if text[offset] == quote:
                    offset += 1
                    break
                offset += 1
            if dynamic:
                # Template expressions execute code. Unsupported interpolation
                # cannot be discarded as inert literal text.
                token = ("unknown_template", "")
            else:
                token = ("literal", None)
        elif char == "/":
            if previous is not None and not (previous[0] == "punct" and previous[1] in regex_prefixes):
                yield ("unknown_code", "")
                return
            end, character_class, closed = offset + 1, False, False
            while end < len(text) and text[end] not in line_ends:
                if text[end] == "\\":
                    if end + 1 >= len(text) or text[end + 1] in line_ends:
                        break
                    end += 2
                    continue
                if text[end] == "[":
                    character_class = True
                elif text[end] == "]":
                    character_class = False
                elif text[end] == "/" and not character_class:
                    closed = True
                    end += 1
                    while end < len(text) and text[end].isalpha():
                        end += 1
                    break
                end += 1
            if not closed:
                yield ("unknown_code", "")
                return
            offset, token = end, ("literal", None)
        elif char == "\\":
            # Escaped identifiers are executable syntax, not inert quoted text.
            # Their meaning is outside this lexer's closed inspection surface.
            offset, token = offset + 1, ("unknown_code", "")
        else:
            match = _KEY.match(text, offset)
            if match is not None:
                offset, token = match.end(), ("name", match[0])
            else:
                offset, token = offset + 1, ("punct", char)
        previous = token
        yield token


def is_batch_input(name, arguments):
    if name not in {"functions.exec", "exec"} or not isinstance(arguments, str):
        return False
    try:
        if len(arguments.encode("utf-8")) > MAX_TEXT:
            return True
        for token in _code_tokens(arguments):
            if token[0] in {"unknown_template", "unknown_code"}:
                return True
            if token == ("name", "Promise"):
                # This is a rejection guard, not another accepted JS grammar.
                # A reference may be grouped, computed, optionally called, or
                # saved as an alias. No call/member/alias evaluation is needed
                # to reject unsupported compositions as fresh origins.
                return True
        return False
    except (ValueError, TypeError, RecursionError, UnicodeError):
        return True


def parse_batch_input(name, arguments):
    """Return only exact observed slot metadata, otherwise no correspondence."""
    if not is_batch_input(name, arguments):
        return None
    try:
        if len(arguments.encode("utf-8")) > MAX_TEXT:
            return None
        reader = _LiteralReader(_PRAGMA.sub("", arguments, count=1))
        # Whitespace is permitted only between these fixed grammar tokens.
        for token in ("const", "results", "=", "await", "Promise.allSettled", "(", "["):
            reader.take(token)
        slots = []
        while True:
            reader.take("tools.")
            match = _KEY.match(reader.text, reader.offset)
            if match is None or match[0] not in {"exec_command", "write_stdin"}:
                return None
            tool, reader.offset = match[0], match.end()
            reader.take("(")
            args = reader.value()
            reader.take(")")
            if not isinstance(args, dict):
                return None
            if tool == "write_stdin":
                handle = args.get("session_id")
                if (not set(args) <= {"session_id", "chars", "yield_time_ms", "max_output_tokens"}
                        or type(handle) is not int or not 0 <= handle < 2**63
                        or int(float(handle)) != handle
                        or ("chars" in args and not isinstance(args["chars"], str))
                        or any(type(args[key]) is not int or args[key] < 0
                               for key in ("yield_time_ms", "max_output_tokens") if key in args)):
                    return None
                slots.append(BatchSlot("poll", ("session", str(handle))))
            else:
                if not set(args) <= _EXEC_FIELDS or not isinstance(args.get("cmd"), str):
                    return None
                slots.append(BatchSlot("exec"))
            if len(slots) > MAX_SLOTS:
                return None
            if reader.peek() == "]":
                break
            reader.take(",")
        for token in ("]", ")", ";", "for", "(", "let", "i", "=", "0", ";", "i", "<",
                      "results.length", ";", "i", "++", ")", "{"):
            reader.take(token)
        reader.space()
        if reader.text.startswith("store", reader.offset):
            reader.take("store")
            reader.take("(")
            reader.string()
            for token in ("+", "i", ",", "results", "[", "i", "]", ")", ";"):
                reader.take(token)
        for token in ("text", "(", "{", "i", ",", "result", ":", "results", "[", "i", "]",
                      "}", ")", ";", "}"):
            reader.take(token)
        if reader.peek() == ";":
            reader.take(";")
        reader.space()
        return tuple(slots) if reader.offset == len(reader.text) else None
    except (ValueError, TypeError, RecursionError, UnicodeError):
        return None


def parse_batch_output(value, count, *, text_parser):
    """Keep indices until correspondence is checked; never inspect opaque bodies.

    Separate complete blocks may be used independently. Conflicting or invalid
    indices reject the complete correspondence, before any result is emitted.
    """
    if type(count) is not int or not 1 <= count <= MAX_SLOTS:
        return None
    seen, result = set(), BatchOutput()

    def visit(item, depth=0):
        if depth > 8:
            return
        if isinstance(item, str):
            try:
                parsed = text_parser(item, unique_keys=True)
            except AmbiguousBatch:
                raise
            except (UsageError, ValueError, TypeError, RecursionError, UnicodeError):
                return
            for child in parsed:
                visit(child, depth + 1)
        elif isinstance(item, list):
            for block in item:
                if (isinstance(block, dict) and block.get("type") in {"text", "input_text"}
                        and isinstance(block.get("text"), str)):
                    visit(block["text"], depth + 1)
        elif isinstance(item, dict):
            if set(item) == {"i", "result"}:
                result.indexed = True
                index, settled = item["i"], item["result"]
                if type(index) is not int or not 0 <= index < count or index in seen:
                    raise ValueError
                seen.add(index)
                if (isinstance(settled, dict) and set(settled) == {"status", "value"}
                        and settled["status"] == "fulfilled"):
                    if depth < 8:
                        result[index] = BatchResult(settled["value"], depth + 1)
            elif set(item) <= _CONTAINER_FIELDS:
                for field in ("output", "content"):
                    if field in item:
                        visit(item[field], depth + 1)

    try:
        visit(value)
        return result
    except (ValueError, TypeError, RecursionError, UnicodeError):
        return None
