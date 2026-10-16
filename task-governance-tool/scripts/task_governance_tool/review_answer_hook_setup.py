"""Explicit Setup-only structured-answer hook definition, no hook execution."""

from copy import deepcopy
import json
import re
import tomllib

from .state_paths import inspect_physical_directory, path_lexically_exists
from .usage_hook_setup import _read, _publish, _unique_object, _reject_constant, MAX_CONFIG_BYTES


MARKER = "taskgov: collect structured review answer"
SERVER = "taskgov_review_wait"
TOOL = "review_answer_collect"


def _allowlist_end(text, start):
    """Locate one parsed string-array value, retaining all surrounding TOML."""
    index, depth, quote, triple, comment = start, 0, None, False, False
    while index < len(text):
        char = text[index]
        if comment:
            comment = char != "\n"
        elif quote is not None:
            delimiter = quote * (3 if triple else 1)
            if text.startswith(delimiter, index):
                index += len(delimiter) - 1
                quote = None
            elif quote == '"' and char == "\\":
                index += 1
        elif char == "#":
            comment = True
        elif char in ("'", '"'):
            quote = char
            triple = text.startswith(char * 3, index)
            if triple:
                index += 2
        elif char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    raise ValueError


def prepare_tool_allowlist(raw):
    """Admit the one existing server/tool; never create launch configuration.

    The parsed before/after comparison is the write boundary. Unsupported TOML
    spelling is unavailable rather than reserializing unrelated configuration.
    """
    if raw is None:
        return None, "missing"
    text = raw.decode("utf-8-sig")
    document = tomllib.loads(text)
    servers = document.get("mcp_servers", {})
    if type(servers) is not dict:
        raise ValueError
    server = servers.get(SERVER)
    if server is None:
        return None, "missing"
    if type(server) is not dict or type(server.get("enabled", True)) is not bool:
        raise ValueError
    # Do not manufacture or repair a process, destination or authorization.
    if not any(type(server.get(key)) is str and server[key] for key in ("command", "url")):
        return None, "missing"
    disabled = server.get("disabled_tools", [])
    if type(disabled) is not list or any(type(item) is not str for item in disabled) or TOOL in disabled:
        raise ValueError
    connection = "configured" if server.get("enabled", True) else "disabled"
    if "enabled_tools" not in server:
        return None, connection
    enabled = server["enabled_tools"]
    if (type(enabled) is not list or any(type(item) is not str for item in enabled)
            or len(set(enabled)) != len(enabled)):
        raise ValueError
    if TOOL in enabled:
        return None, connection
    tables = list(re.finditer(r"(?m)^[ \t]*\[mcp_servers\.taskgov_review_wait\][ \t]*(?:#[^\r\n]*)?\r?$", text))
    if len(tables) != 1:
        raise ValueError
    table = tables[0]
    next_table = re.search(r"(?m)^[ \t]*\[", text[table.end():])
    end = table.end() + next_table.start() if next_table else len(text)
    entries = list(re.finditer(r"(?m)^[ \t]*enabled_tools[ \t]*=[ \t]*", text[table.end():end]))
    if len(entries) != 1:
        raise ValueError
    start = table.end() + entries[0].end()
    if start >= len(text) or text[start] != "[":
        raise ValueError
    finish = _allowlist_end(text, start)
    if finish > end:
        raise ValueError
    replacement = json.dumps([*enabled, TOOL], ensure_ascii=False)
    updated = text[:start] + replacement + text[finish:]
    expected = deepcopy(document)
    expected["mcp_servers"][SERVER]["enabled_tools"].append(TOOL)
    if tomllib.loads(updated) != expected:
        raise ValueError
    encoded = updated.encode("utf-8")
    if raw.startswith(b"\xef\xbb\xbf"):
        encoded = b"\xef\xbb\xbf" + encoded
    if len(encoded) > MAX_CONFIG_BYTES:
        raise ValueError
    return encoded, connection


def _owned(handler):
    return type(handler) is dict and (handler.get("statusMessage") == MARKER
        or handler.get("server") == SERVER and handler.get("tool") == TOOL)


def _contains_owned(value):
    if type(value) is dict:
        return _owned(value) or any(_contains_owned(item) for item in value.values())
    return type(value) is list and any(_contains_owned(item) for item in value)


def merge_definition(document):
    if type(document) is not dict:
        raise ValueError
    result = deepcopy(document)
    events = result.setdefault("hooks", {})
    if type(events) is not dict:
        raise ValueError
    # Official MCP-hook interpolation preserves each event field's JSON type.
    # Forward only identity hints; never the assistant body or transcript path.
    event = {key: "${" + key + "}" for key in
             ("hook_event_name", "session_id", "agent_id", "turn_id", "stop_hook_active")}
    desired = dict(type="mcp_tool", server=SERVER, tool=TOOL, input={"event": event}, timeout=30, statusMessage=MARKER)
    found = False
    for event, groups in events.items():
        if type(groups) is not list:
            raise ValueError
        for group in groups:
            if type(group) is not dict or type(group.get("hooks")) is not list:
                raise ValueError
            handlers = []
            for handler in group["hooks"]:
                if type(handler) is not dict:
                    raise ValueError
                if _owned(handler):
                    if (event != "SubagentStop" or group.get("matcher") not in (None, "", "*")
                            or handler != desired):
                        raise ValueError
                    if not found:
                        handlers.append(desired)
                        found = True
                else:
                    handlers.append(handler)
            group["hooks"] = handlers
    if not found:
        events.setdefault("SubagentStop", []).append({"hooks": [desired]})
    return result


def setup_answer_hook(root, *, enabled, read_only, diagnostic=None):
    """Return physical definition state only; server/trust remain unknown.

    Shared config publication primitives are reused, not numerical collection.
    Existing inline definitions are never silently duplicated or rewritten.
    """
    if diagnostic is None:
        diagnostic = {}
    diagnostic.update(status="disabled", planned_writes=[], completed_writes=[],
                      connection="unknown", trust="unknown", error=None)
    if enabled is not True:
        return "off", False
    diagnostic["status"] = "unavailable"
    diagnostic["error"] = "review_answer_setup_unavailable"
    directory = root / ".codex"
    if path_lexically_exists(directory):
        inspect_physical_directory(directory, root=root)
    inline = _read(directory / "config.toml", root) if directory.exists() else None
    if inline and _contains_owned(tomllib.loads(inline[0].decode("utf-8-sig")).get("hooks", {})):
        raise ValueError
    allowlist, connection = prepare_tool_allowlist(inline[0] if inline else None)
    diagnostic["connection"] = connection
    path = directory / "hooks.json"
    original = _read(path, root) if directory.exists() else None
    document = json.loads(original[0].decode("utf-8-sig"), object_pairs_hook=_unique_object,
        parse_constant=_reject_constant) if original is not None else {}
    merged = merge_definition(document)
    changed = merged != document
    writes = diagnostic["planned_writes"]
    if changed:
        writes.append("review_answer_hook_prepare")
    if allowlist is not None:
        writes.append("review_answer_tool_allowlist_prepare")
    if changed:
        raw = (json.dumps(merged, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
        if len(raw) > MAX_CONFIG_BYTES:
            raise ValueError
        if not read_only:
            _publish(path, root, original, raw)
            diagnostic["completed_writes"].append("review_answer_hook_prepare")
    if allowlist is not None and not read_only:
        _publish(directory / "config.toml", root, inline, allowlist)
        diagnostic["completed_writes"].append("review_answer_tool_allowlist_prepare")
    diagnostic["status"] = ("connection_required" if connection != "configured" else
        "preparation_required" if writes and read_only else "prepared" if writes else "current")
    diagnostic["error"] = None
    definition = "off" if changed and read_only else "on"
    if connection != "configured" or allowlist is not None and read_only:
        definition = "off"
    return definition, changed or allowlist is not None
