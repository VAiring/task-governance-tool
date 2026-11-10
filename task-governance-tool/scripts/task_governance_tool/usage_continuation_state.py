"""Closed, coalesced Usage work intent; no source bodies or executable input.

The state lock serializes intake and acknowledgement. The execution lease is
separate and covers numerical work, not just a launcher. Only an admitted
caller may create this resolver-owned operational directory.
"""

from contextlib import contextmanager
import json
import os
import re
from uuid import uuid4

from .artifact_lock import zero_wait_artifact_lock
from .state_paths import (create_exclusive_durable_file, create_physical_directory_exclusive,
    inspect_physical_directory, inspect_physical_file, path_lexically_exists,
    read_physical_file_bounded, unlink_validated_file)
from .usage_values import UsageError

MAX_BYTES = 8 * 1024 * 1024
STATUSES = frozenset({"requested", "pending", "caught_up", "waiting_input",
                      "waiting_review", "budget_exhausted", "unavailable"})
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_TOKEN = re.compile(r"[0-9a-f]{32}\Z")
_FIELDS = {"v", "project_id", "binding_path_hash", "binding_generation", "roots",
           "request", "round", "status", "intakes"}


def _fail():
    return UsageError()


def _integer(value):
    return type(value) is int and 0 <= value < 2**63


def _hex(value, pattern):
    return type(value) is str and pattern.fullmatch(value) is not None


def validate(value):
    if (type(value) is not dict or set(value) != _FIELDS
            or type(value["v"]) is not int or value["v"] != 1
            or type(value["project_id"]) is not str
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", value["project_id"]) is None
            or not _hex(value["binding_path_hash"], _HASH)
            or not _integer(value["binding_generation"]) or value["binding_generation"] < 1
            or not _hex(value["request"], _TOKEN)
            or type(value["status"]) is not str or value["status"] not in STATUSES
            or type(value["roots"]) is not dict or set(value["roots"]) != {"sessions", "archive"}
            or any(not _hex(item, _HASH) for item in value["roots"].values())):
        raise _fail()
    round_ = value["round"]
    if round_ is not None and (type(round_) is not dict
            or set(round_) != {"epoch", "identity", "generation", "inventory_cycle", "phase", "after_order", "after_source", "through", "reason"}
            or not _hex(round_["epoch"], _TOKEN)
            or not _hex(round_["identity"], _HASH) or not _integer(round_["generation"])
            or not _integer(round_["inventory_cycle"])
            or type(round_["phase"]) is not str or round_["phase"] not in {"intake", "review", "inventory", "sources", "publish", "confirm"}
            or not _integer(round_["after_order"]) or not _integer(round_["through"])
            or round_["after_order"] > round_["through"]
            or not (round_["after_source"] == "" or _hex(round_["after_source"], _HASH))
            or type(round_["reason"]) is not str or round_["reason"] not in STATUSES - {"caught_up", "requested"}):
        raise _fail()
    if value["status"] == "caught_up" and (round_ is None or round_["phase"] not in {"publish", "confirm"}):
        raise _fail()
    from .usage_continuation_intake import validate as validate_intake
    if type(value["intakes"]) is not list or len(value["intakes"]) > 1024:
        raise _fail()
    for item in value["intakes"]:
        validate_intake(item)
    if value["status"] == "caught_up" and value["intakes"]:
        raise _fail()
    return value


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise _fail()
        result[key] = value
    return result


def root_for(target, *, create=False):
    root = target.resolved_usage_runtime
    for path in (root.parent, root):
        if create and not path_lexically_exists(path):
            try:
                create_physical_directory_exclusive(path, root=target.db_path.parent)
            except Exception:
                inspect_physical_directory(path, root=target.db_path.parent)
        inspect_physical_directory(path, root=target.db_path.parent)
    return root


@contextmanager
def lease(target, name, *, create=False):
    if name not in {"state", "execution", "service"}:
        raise _fail()
    root = root_for(target, create=create)
    identity = inspect_physical_directory(root).identity
    with zero_wait_artifact_lock(root / (name + ".lock"), require_single_link=True):
        if inspect_physical_directory(root).identity != identity:
            raise _fail()
        yield root
        if inspect_physical_directory(root).identity != identity:
            raise _fail()


def read(root, target):
    """Caller holds state lease; malformed state never authorizes replacement."""
    path = root / "continuation.json"
    if not path_lexically_exists(path):
        return None
    if path.lstat().st_nlink != 1:
        raise _fail()
    data, _ = read_physical_file_bounded(path, root=root, max_bytes=MAX_BYTES)
    try:
        value = validate(json.loads(data.decode("ascii"), object_pairs_hook=_unique))
    except (ValueError, TypeError, UnicodeError):
        raise _fail() from None
    if any(value[key] != expected for key, expected in (
            ("project_id", target.project.project_id),
            ("binding_path_hash", target.binding_path_hash),
            ("binding_generation", target.binding_generation))):
        raise _fail()
    return value


def write(root, value):
    """Caller holds state lease; recover only the reserved bounded temporary."""
    data = json.dumps(validate(value), ensure_ascii=True, sort_keys=True,
                      separators=(",", ":")).encode("ascii")
    if len(data) > MAX_BYTES:
        raise _fail()
    directory = inspect_physical_directory(root).identity
    path, temporary = root / "continuation.json", root / ".continuation-next.json"
    if path_lexically_exists(temporary):
        _, observed = read_physical_file_bounded(temporary, root=root, max_bytes=MAX_BYTES)
        if temporary.lstat().st_nlink != 1:
            raise _fail()
        unlink_validated_file(observed, root=root)
    create_exclusive_durable_file(temporary, data, root=root, max_bytes=MAX_BYTES)
    if inspect_physical_directory(root).identity != directory:
        raise _fail()
    if path_lexically_exists(path):
        inspect_physical_file(path, root=root, max_bytes=MAX_BYTES)
        if path.lstat().st_nlink != 1:
            raise _fail()
    os.replace(temporary, path)


def request(root, target, roots, *, intake=None):
    # Validate the old binding before replacing its operational intent.
    previous = read(root, target)
    intakes = [] if previous is None else list(previous["intakes"])
    if intake is not None and intake not in intakes:
        intakes.append(intake)
    value = dict(v=1, project_id=target.project.project_id,
        binding_path_hash=target.binding_path_hash, binding_generation=target.binding_generation,
        roots=dict(roots), request=uuid4().hex, round=None, status="requested", intakes=intakes)
    write(root, value)
    return value


def update(target, expected, *, round_, status, intakes=None):
    with lease(target, "state") as root:
        current = read(root, target)
        if current != expected:
            return None
        result = {**current, "round": round_, "status": status,
                  "intakes": current["intakes"] if intakes is None else intakes}
        if result != current:
            write(root, result)
        return result
