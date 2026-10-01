"""Project-local usage hook preparation, invoked only by explicit outer setup."""

from __future__ import annotations

from copy import deepcopy
from contextlib import suppress
import base64
import json
import os
from os import replace as replace_configuration
from pathlib import Path
import shlex
import sys
import tomllib
from uuid import uuid4

from task_governance_tool.no_replace import rename_no_replace
from task_governance_tool.state_paths import (
    create_exclusive_durable_file, create_physical_directory_exclusive,
    inspect_physical_directory, path_lexically_exists,
    read_physical_file_bounded, unlink_validated_file,
)

EVENTS = {"SessionStart": 30, "Stop": 30, "SubagentStop": 30, "SessionEnd": 3}
MARKER = "taskgov: collect numerical usage"
MAX_CONFIG_BYTES = 1024 * 1024


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate configuration key")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError("nonstandard JSON constant")


def _read(path: Path, root: Path):
    if not path_lexically_exists(path):
        return None
    return read_physical_file_bounded(path, root=root, max_bytes=MAX_CONFIG_BYTES)


def _commands(root: Path, skill_root: Path) -> tuple[str, str]:
    script = skill_root / "scripts" / "usage_hook.py"
    relative = script.relative_to(root).as_posix()
    # The Windows host uses commandWindows; retain a portable POSIX alternative.
    command = (shlex.join(["python3", "-B", relative, "--repo", "."])
               if os.name == "nt" else
               shlex.join([sys.executable, "-B", str(script), "--repo", str(root)]))
    def ps_quote(value):
        # PowerShell single-quote delimiters include typographic apostrophes.
        return "'" + "".join(char * 2 if char in "'\u2018\u2019\u201a\u201b" else char
                             for char in str(value)) + "'"
    body = ('& '
               + ps_quote(Path(sys.executable).as_posix()) + " -B "
               + ps_quote(script.as_posix()) + " --repo " + ps_quote(root.as_posix()))
    # Encode only this fixed invocation so a caller shell cannot expand path
    # characters ($, quotes, backticks) before PowerShell receives them.
    windows = ("powershell.exe -NoProfile -NonInteractive -EncodedCommand "
               + base64.b64encode(body.encode("utf-16-le")).decode("ascii"))
    return command, windows


def _usage_handler(handler) -> bool:
    return isinstance(handler, dict) and (
        handler.get("statusMessage") == MARKER
        or any("usage_hook.py" in os.path.normcase(str(handler.get(key, "")))
               for key in ("command", "commandWindows", "command_windows")))


def _contains_usage(value) -> bool:
    if isinstance(value, dict):
        return _usage_handler(value) or any(_contains_usage(item) for item in value.values())
    return isinstance(value, list) and any(_contains_usage(item) for item in value)


def _merge(document: dict, root: Path, skill_root: Path) -> dict:
    if not isinstance(document, dict):
        raise ValueError("configuration must be an object")
    result = deepcopy(document)
    events = result.setdefault("hooks", {})
    if not isinstance(events, dict):
        raise ValueError("hooks must be an object")
    command, windows = _commands(root, skill_root)
    relative = (skill_root / "scripts" / "usage_hook.py").relative_to(root).as_posix()
    # Exact previous documented recipes only, not a shell/relation parser.
    legacy = {(f"python3 -B {relative}", f"python -B {relative}"),
              (f"python3 -B {relative} --repo .", windows)}
    for event, groups in events.items():
        if not isinstance(groups, list):
            raise ValueError("event must contain groups")
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                raise ValueError("invalid hook group")
            for handler in group["hooks"]:
                if not isinstance(handler, dict):
                    raise ValueError("invalid hook handler")
                if not _usage_handler(handler):
                    continue
                known = (handler.get("statusMessage") == MARKER or
                         (set(handler) == {"type", "command", "commandWindows", "timeout"}
                          and (handler.get("command"), handler.get("commandWindows")) in legacy
                          and handler.get("timeout") == EVENTS.get(event)))
                if (not known or event not in EVENTS
                        or group.get("matcher") not in (None, "", "*")
                        or handler.get("type") != "command"):
                    raise ValueError("manual usage hook needs review")
    for event, timeout in EVENTS.items():
        desired = {"type": "command", "command": command, "commandWindows": windows,
                   "timeout": timeout, "statusMessage": MARKER}
        found = False
        groups = events.setdefault(event, [])
        for group in groups:
            handlers = []
            for handler in group["hooks"]:
                if _usage_handler(handler):
                    if not found:
                        handlers.append(desired)
                        found = True
                else:
                    handlers.append(handler)
            group["hooks"] = handlers
        if not found:
            groups.append({"hooks": [desired]})
    return result


def _publish(path: Path, root: Path, original, data: bytes) -> None:
    if not path_lexically_exists(path.parent):
        create_physical_directory_exclusive(path.parent, root=root)
    inspect_physical_directory(path.parent, root=root)
    temporary = create_exclusive_durable_file(
        path.with_name(".taskgov-hooks-" + uuid4().hex + ".tmp"), data,
        root=root, max_bytes=MAX_CONFIG_BYTES)
    try:
        # Preserve a concurrently edited configuration instead of merging stale bytes.
        if _read(path, root) != original:
            raise ValueError("configuration changed during setup")
        if original is None:
            rename_no_replace(temporary, path, root=root)
        else:
            replace_configuration(temporary.path, path)
    finally:
        if path_lexically_exists(temporary.path):
            with suppress(OSError, ValueError):
                unlink_validated_file(temporary, root=root)


def setup_usage_hooks(inspection, core_result, *, read_only: bool) -> dict:
    """Prepare definitions, never invoke collection or read/write host trust."""
    result = {"status": "not_attempted", "planned_writes": [], "completed_writes": [],
              "trust": "unknown", "next_action": None, "error": None}
    if not core_result.ok or inspection.scope is None:
        return result
    if core_result.data["usage"]["status"] not in {
            "pending_core_setup", "not_present", "migration_required",
            "initialized", "migrated", "current"}:
        return result
    try:
        scope = inspection.scope
        root, skill_root = scope.canonical_repo, scope.skill_root
        directory = root / ".codex"
        if path_lexically_exists(directory):
            inspect_physical_directory(directory, root=root)
        # Inline project hooks also run. Do not silently add a second collector.
        inline = _read(directory / "config.toml", root) if directory.exists() else None
        if inline and _contains_usage(tomllib.loads(inline[0].decode("utf-8-sig")).get("hooks", {})):
            raise ValueError("inline usage hook needs review")
        path = directory / "hooks.json"
        original = _read(path, root) if directory.exists() else None
        document = (json.loads(original[0].decode("utf-8-sig"), object_pairs_hook=_unique_object,
                               parse_constant=_reject_constant)
                    if original is not None else {})
        merged = _merge(document, root, skill_root)
        result.update(status="current", next_action="review_and_trust_hooks")
        if merged != document:
            data = (json.dumps(merged, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
            if len(data) > MAX_CONFIG_BYTES:
                raise ValueError("configuration too large")
            result.update(status="preparation_required", planned_writes=["usage_hooks_prepare"])
            if not read_only:
                _publish(path, root, original, data)
                result.update(status="prepared", completed_writes=["usage_hooks_prepare"])
    except Exception:
        result.update(status="unavailable", next_action="review_hook_configuration",
                      error="usage_hooks_unavailable")
    return result
