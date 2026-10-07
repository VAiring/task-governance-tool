"""Bounded local Setup choices and physical configuration publication.

Choices record intent, not host trust or runtime success. Usage collection and
review waiting consult saved switches; other features retain their own configs.
"""

import json
from contextlib import suppress
from os import replace
from pathlib import Path
from uuid import uuid4

from task_governance_tool.no_replace import rename_no_replace
from task_governance_tool.state_paths import (
    create_exclusive_durable_file, create_physical_directory_exclusive,
    inspect_physical_directory, path_lexically_exists,
    read_physical_file_bounded, unlink_validated_file,
)

FEATURES = ("usage_collection", "verification_runner", "effort_advisory", "viewer_reload", "review_wait")
CHOICES_PATH = Path("config/setup-features.json")
MAX_BYTES = 16 * 1024


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate configuration key")
        result[key] = value
    return result


def _constant(_value):
    raise ValueError("nonstandard JSON number")


def decode(raw):
    return json.loads(raw.decode("utf-8"), object_pairs_hook=_unique, parse_constant=_constant)


def encode(document):
    raw = (json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    if len(raw) > MAX_BYTES:
        raise ValueError("configuration too large")
    return raw


def read(path, root):
    inspect_physical_directory(root)
    if path_lexically_exists(path.parent):
        inspect_physical_directory(path.parent, root=root)
    if not path_lexically_exists(path):
        return None
    return read_physical_file_bounded(path, root=root, max_bytes=MAX_BYTES)


def publish(path, root, original, raw):
    """Single-file compare-before-replace; never merge concurrent configuration."""
    if not path_lexically_exists(path.parent):
        create_physical_directory_exclusive(path.parent, root=root)
    inspect_physical_directory(path.parent, root=root)
    temporary = create_exclusive_durable_file(
        path.with_name(".taskgov-features-" + uuid4().hex + ".tmp"), raw,
        root=root, max_bytes=MAX_BYTES)
    try:
        if read(path, root) != original:
            raise ValueError("configuration changed")
        if original is None:
            rename_no_replace(temporary, path, root=root)
        else:
            replace(temporary.path, path)
    finally:
        if path_lexically_exists(temporary.path):
            with suppress(OSError, ValueError):
                unlink_validated_file(temporary, root=root)


def read_choices(skill_root):
    original = read(skill_root / CHOICES_PATH, skill_root)
    if original is None:
        return original, {}
    value = decode(original[0])
    if (not isinstance(value, dict) or set(value) != {"schema_version", "choices"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or not isinstance(value["choices"], dict)
            or set(value["choices"]) - set(FEATURES)
            or any(type(v) is not bool for v in value["choices"].values())):
        raise ValueError("invalid Setup choices")
    return original, value["choices"]


def collection_allowed(skill_root):
    # Legacy user-installed hooks keep working without a newly invented opt-in.
    # Fresh setup creates no collector definitions until explicitly selected ON.
    try:
        _original, choices = read_choices(Path(skill_root))
        return choices.get("usage_collection", True)
    except Exception:
        return False
