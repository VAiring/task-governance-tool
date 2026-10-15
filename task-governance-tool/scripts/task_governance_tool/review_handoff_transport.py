"""Shared bounded handoff transport and pure Packet/result validation.

Callers own admission, live-state checks and effects. These operations never
repair input, register evidence, infer permission or retry an uncertain write.
"""

import json
import os
import stat
import subprocess
import sys

from .completion import safe_git_command, safe_git_environment
from .review_results import decode_review_results, normalize_review_results, review_result_template
from .reviews import REVIEW_TARGET_KINDS, validate_stored_review_target
from .task_values import (SQLITE_INT64_MAX, reject_private_or_raw_content,
                          validate_legacy_m19_7_stored_text, validate_text)
from .verification_declaration import verification_requirement
from . import review_session_transport as session_transport


PACKET_LIMIT = 32768


_PACKET_KEYS = {
    "task", "contract", "review_target", "changed_paths_available", "changed_paths",
    "changed_paths_total", "changed_paths_truncated", "review_focus", "required_output",
    "result_template", "result_instructions", "receipt_command",
}


class HandoffError(Exception):
    def __init__(self, code="handoff_invalid_input"):
        self.code = code


def fail(code="handoff_invalid_input"):
    raise HandoffError(code)


def file_identity(details, *, change_time=False):
    # Windows Python exposes different ctime meanings through lstat/fstat.
    # Compare ctime only between observations using the same API; cross-API
    # checks retain object identity, mode, links, size and nanosecond mtime.
    return (details.st_dev, details.st_ino, details.st_mode, details.st_nlink,
            details.st_size, details.st_mtime_ns) + ((details.st_ctime_ns,) if change_time else ())


def require_physical(details, *, directory=False):
    if (stat.S_ISLNK(details.st_mode)
            or getattr(details, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            or not (stat.S_ISDIR if directory else stat.S_ISREG)(details.st_mode)
            or (not directory and details.st_nlink != 1)):
        fail("handoff_path_unsafe")


def observe_parents(path):
    """Observe the whole existing physical ancestor chain, including repo ancestors."""
    observed = []
    for parent in reversed(path.parents):
        details = parent.lstat()
        require_physical(details, directory=True)
        observed.append((parent, details.st_dev, details.st_ino))
    return observed


def check_parents(observed):
    for parent, device, inode in observed:
        details = parent.lstat()
        require_physical(details, directory=True)
        if (details.st_dev, details.st_ino) != (device, inode):
            fail("handoff_file_changed")


def relative_path(repo, supplied):
    # Portable project-relative names only: no ADS, device names, traversal,
    # reserved state/package areas or invisible path spellings.
    reject_private_or_raw_content("review_result_path", supplied)
    if (not supplied or len(supplied.encode("utf-8")) > 4096
            or "\\" in supplied or any(ord(c) < 32 for c in supplied)):
        fail("handoff_path_unsafe")
    parts = supplied.split("/")
    reserved = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(10)), *(f"lpt{i}" for i in range(10))}
    if any(not p or p in {".", ".."} or p.endswith((".", " "))
           or any(c in p for c in ':*?"<>|') or p.split(".")[0].lower() in reserved for p in parts):
        fail("handoff_path_unsafe")
    if parts[0].lower() in {".git", ".agents", ".codex", ".taskgov", "task-governance-tool"}:
        fail("handoff_path_unsafe")
    return repo.joinpath(*parts)


def require_ignored(repo, supplied):
    # --no-index is intentionally absent: tracked paths must not qualify.
    result = subprocess.run(
        [*safe_git_command(repo), "-c", "core.fsmonitor=false", "check-ignore", "--quiet", "--", supplied],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        timeout=2, shell=False, env=safe_git_environment(), check=False,
    )
    if result.returncode != 0:
        fail("handoff_ignore_required")


def checked_path(repo, supplied):
    path = relative_path(repo, supplied)
    if path.suffix.lower() != ".json":
        fail("handoff_path_unsafe")
    observe_parents(path)
    require_ignored(repo, supplied)
    return path


def read_bounded(path, limit):
    parents = observe_parents(path)
    before = path.lstat()
    require_physical(before)
    if before.st_size > limit:
        fail("handoff_input_too_large")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        require_physical(opened)
        if file_identity(opened) != file_identity(before):
            fail("handoff_file_changed")
        chunks = []
        size = 0
        while size <= limit:
            chunk = os.read(descriptor, min(65536, limit + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
        after = os.fstat(descriptor)
        if size != before.st_size or file_identity(after, change_time=True) != file_identity(opened, change_time=True):
            fail("handoff_file_changed")
    finally:
        os.close(descriptor)
    check_parents(parents)
    final = path.lstat()
    require_physical(final)
    if file_identity(final, change_time=True) != file_identity(before, change_time=True):
        fail("handoff_file_changed")
    return b"".join(chunks)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            fail()
        result[key] = value
    return result


def validate_packet_target(value):
    if (type(value) is not dict or set(value) != {"kind", "value", "base_revision", "generation"}
            or type(value["generation"]) is not int or not 1 <= value["generation"] <= SQLITE_INT64_MAX
            or any(type(value[name]) is not str for name in ("kind", "value", "base_revision"))
            or value["kind"] not in REVIEW_TARGET_KINDS):
        fail("handoff_response_invalid")
    for name in ("kind", "value", "base_revision"):
        validate_text("review_target_" + name, value[name], required=name != "base_revision", limit=500)
    validate_stored_review_target({"review_target_" + key: item for key, item in value.items()})


def decode_packet(raw, expected_task_id=None):
    """Pure complete-Packet validation shared by every handoff entry point.

    Stored compatibility is constraints-only. This neither repairs input nor
    checks live state; reviewer read and registration retain those boundaries.
    """
    if len(raw) > PACKET_LIMIT:
        fail("handoff_input_too_large")
    packet = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object,
                        parse_constant=lambda value: fail())
    if (type(packet) is not dict or not _PACKET_KEYS <= set(packet)
            or set(packet) - _PACKET_KEYS - {"review_session_context", "verification_evidence"}):
        fail()
    if "verification_evidence" in packet:
        from task_governance_tool.review_verification import validate_review_verification
        try:
            validate_review_verification(packet["verification_evidence"])
        except (ValueError, TypeError, KeyError):
            fail("handoff_response_invalid")
    if "review_session_context" in packet:
        session_transport.validate_context(packet["review_session_context"], allow_unknown_execution=True)
    task, contract, target = packet["task"], packet["contract"], packet["review_target"]
    if (type(task) is not dict or type(contract) is not dict or type(target) is not dict
            or type(task.get("task_id")) is not str or type(task.get("review_tier")) is not int
            or task["review_tier"] not in (0, 1, 2) or type(contract.get("revision")) is not int
            or type(target.get("generation")) is not int):
        fail()
    expected = review_result_template(task["task_id"], contract["revision"], target)
    if json.dumps(packet["result_template"], sort_keys=True) != json.dumps(expected, sort_keys=True):
        fail()
    if (set(task) != {
            "task_id", "title", "status", "verification", "verification_not_required_reason", "review_tier"}
            or set(contract) not in ({"revision", "scope", "acceptance", "constraints"},
                                    {"revision", "scope", "acceptance", "constraints", "authority_ref"})
            or (expected_task_id is not None and task["task_id"] != expected_task_id)):
        fail("review_target_mismatch")
    validate_packet_target(target)
    reason = task["verification_not_required_reason"]
    if type(reason) is not str:
        fail("handoff_response_invalid")
    validate_text("verification_not_required_reason", reason, limit=1000)
    if not 0 <= contract["revision"] <= SQLITE_INT64_MAX:
        fail("handoff_response_invalid")
    # Older saved Packets omit this context field. Never fill it or resolve it
    # as a path; supplied references retain the ordinary privacy/size boundary.
    if "authority_ref" in contract:
        reference = contract["authority_ref"]
        if type(reference) is not str or "\n" in reference or "\r" in reference:
            fail("handoff_response_invalid")
        validate_text("contract_authority_ref", reference, limit=500)
    for owner, names in ((task, ("title", "status", "verification")),
                         (contract, ("scope", "acceptance", "constraints"))):
        for name in names:
            if type(owner[name]) is not str:
                fail("handoff_response_invalid")
            if owner is contract:
                validator = validate_legacy_m19_7_stored_text if name == "constraints" else validate_text
                validator("contract_" + name, owner[name])
            else:
                validate_text(name, owner[name])
    verification_requirement(task["verification"], reason)
    for name in ("review_focus", "required_output", "result_instructions"):
        if (type(packet[name]) is not list or not packet[name]
                or any(type(item) is not str or not item for item in packet[name])):
            fail("handoff_response_invalid")
    if type(packet["receipt_command"]) is not str or not packet["receipt_command"]:
        fail("handoff_response_invalid")
    if (type(packet["changed_paths"]) is not list
            or any(type(item) is not str for item in packet["changed_paths"])
            or type(packet["changed_paths_available"]) is not bool
            or type(packet["changed_paths_truncated"]) is not bool
            or type(packet["changed_paths_total"]) is not int
            or packet["changed_paths_total"] < len(packet["changed_paths"])
            or (not packet["changed_paths_truncated"] and packet["changed_paths_total"] != len(packet["changed_paths"]))):
        fail("handoff_response_invalid")
    return packet


def validate_results(raw, packet, approvals, *, original=False):
    payload = decode_review_results(raw)
    normalize_review_results(payload, review_tier=packet["task"]["review_tier"],
                             user_approved_reviewers=approvals)
    if original and (not raw.lstrip().startswith(b"{") or len(payload["receipts"]) != 1):
        fail()
    expected = packet["result_template"]
    if any(payload[key] != expected[key] for key in ("version", "task_id", "contract_revision", "review_target")):
        fail("review_target_mismatch")
    return payload


def write_exclusive(path, raw):
    parents = observe_parents(path)
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        require_physical(os.fstat(descriptor))
        check_parents(parents)
        offset = 0
        while offset < len(raw):
            written = os.write(descriptor, raw[offset:])
            if written <= 0:
                fail("handoff_io_failed")
            offset += written
        os.fsync(descriptor)
        created = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    check_parents(parents)
    if file_identity(path.lstat()) != file_identity(created):
        fail("handoff_file_changed")


def emit_json(value, *, utf8=False):
    try:
        if utf8:
            # The reviewer display replaces a UTF-8 file read. Avoid expanding
            # non-ASCII Contract prose or depending on the shell code page.
            sys.stdout.buffer.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n")
            sys.stdout.buffer.flush()
        else:
            print(json.dumps(value, ensure_ascii=True, separators=(",", ":")), flush=True)
        return True
    except OSError:
        # A lost acknowledgement cannot undo the file write. Do not retry it
        # or raise another traceback while trying to print the error response.
        try:
            sys.stdout.close()
        except OSError:
            pass
        return False
