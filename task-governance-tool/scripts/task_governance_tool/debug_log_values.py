"""Bounded diagnostic projections; never call user str/repr or retain raw values."""
from __future__ import annotations

import builtins
import math
from pathlib import Path
import re
from types import FunctionType

MISSING = object()
_TASK_ID = re.compile(r"tg_task_[0-9a-f]{16}\Z")
_SECRET = re.compile(r"password|passwd|passphrase|secret|credential|token|cookie|authorization|authentication|"
                     r"(?:api|access|private|session|ssh)[_ -]?key", re.I)
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_AUTH = re.compile(r"(?:^|[^a-z])(?:pin|otp|totp|hotp|pwd|passcode|cvv|cvc)(?:[^a-z]|$)|"
                   r"(?:auth|verification|security|recovery|backup|mfa|2fa|one[_ -]?time)[_ -]?code", re.I)
_NAME = re.compile(r"[A-Za-z_][A-Za-z_0-9]{0,79}\Z")
_PACKAGE = Path(__file__).parent


def secret_field(name):
    if type(name) is not str:
        return True
    if len(name) > 256:
        return True
    return bool(_SECRET.search(name) or _AUTH.search(_CAMEL.sub('_', name)))


def plain_fields(mapping):
    """Own a bounded snapshot before lookup; foreign keys may run __eq__."""
    if type(mapping) is not dict or len(mapping) > 256:
        return None
    fields = {}
    for index, (key, item) in enumerate(dict.items(mapping)):
        if index >= 256 or type(key) is not str:
            return None
        fields[key] = item
    return fields


def value(item, *, secret=False, depth=0):
    """Only exact built-in containers/scalars are traversed, with closed limits."""
    if item is MISSING:
        return {"state": "missing"}
    if item is None:
        return {"state": "null"}
    kind = type(item)
    if kind is str:
        if not item:
            return {"state": "empty_string"}
        # Avoid scanning unbounded input just to classify whitespace.
        if len(item) <= 4096 and item.isspace():
            return {"state": "whitespace"}
    if secret:
        return {"state": "present", "redacted": True}
    if kind is bool or kind is int and item.bit_length() <= 256:
        return {"state": "present", "value": item}
    if kind is float:
        return ({"state": "present", "value": item} if math.isfinite(item)
                else {"state": "non_finite"})
    if kind is str:
        if len(item) == 24 and _TASK_ID.fullmatch(item):
            return {"state": "present", "value": item}
        return {"state": "present", "redacted": True}
    if kind is list or kind is tuple or kind is dict:
        if depth >= 3:
            return {"state": "present", "truncated": True}
        if kind is dict:
            projected = []
            for index, (key, child) in enumerate(item.items()):
                if index == 16:
                    break
                sensitive = secret_field(key)
                projected.append({"key": value(key, depth=depth + 1),
                                  "value": value(child, secret=sensitive, depth=depth + 1)})
        else:
            projected = [value(child, depth=depth + 1) for child in item[:16]]
        return {"state": "present", "items": projected, "truncated": len(item) > 16}
    return {"state": "present", "kind": "object", "redacted": True}


def callable_metadata(function):
    """Metadata comes from a source function, never from runtime label strings."""
    if type(function) is not FunctionType:
        return "other", (), False
    code = function.__code__
    label = code_location(code).get("function", "other")
    # Parameter names are code-owned; **kwargs dynamic keys are never expanded
    # into field names. Avoid inspect.signature evaluation of annotations.
    count = code.co_argcount + code.co_kwonlyargcount
    names = code.co_varnames[:min(count, 32)]
    return label, tuple(name for name in names if type(name) is str and _NAME.fullmatch(name)), count > 32


def arguments(function, supplied):
    _, names, truncated = callable_metadata(function)
    fields = plain_fields(supplied)
    if fields is None:
        return {"args": {}, "args_truncated": True}
    return {"args": {name: value(fields.get(name, MISSING), secret=secret_field(name))
                     for name in names if name not in {"self", "cls"}},
            "args_truncated": truncated}


def code_location(code):
    try:
        # Code metadata is lexical: no source probing while a DB is open.
        if type(code.co_filename) is not str or type(code.co_name) is not str:
            return {}
        relative = Path(code.co_filename).relative_to(_PACKAGE)
        if (relative.suffix != ".py" or not all(_NAME.fullmatch(p) for p in relative.with_suffix("").parts)
                or not _NAME.fullmatch(code.co_name)):
            return {}
        return {"module": ".".join(relative.with_suffix("").parts), "function": code.co_name}
    except (OSError, ValueError):
        return {}


def exception(error):
    """No message, args, locals, source text or arbitrary exception attributes."""
    kind = type(error)
    name = next((name for name in ("ValueError", "TypeError", "RuntimeError", "OSError",
                "KeyError", "TimeoutError", "KeyboardInterrupt", "SystemExit")
                if kind is getattr(builtins, name)), "Exception")
    frames = []
    # Calling __getattribute__ still invokes subclass descriptors. The native
    # descriptor reads only the actual exception traceback slot.
    trace = BaseException.__traceback__.__get__(error)
    visited = 0
    while trace is not None and visited < 64 and len(frames) < 20:
        location = code_location(trace.tb_frame.f_code)
        if location:
            frames.append({**location, "line": trace.tb_lineno})
        trace = trace.tb_next
        visited += 1
    return {"kind": name, "stack": frames, "truncated": trace is not None,
            "message": {"state": "omitted"}}
