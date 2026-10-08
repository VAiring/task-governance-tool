"""Bounded caller-owned transport; no core writer or replacement review gate.

Save validates before exclusive creation and retains any failed-write residue.
Submit frames unchanged originals and delegates to the existing public stdin
writer, whose current-state checks remain authoritative. Neither operation
infers approval, repairs input, overwrites, deletes, or retries.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

from task_governance_tool.completion import safe_git_command, safe_git_environment
from task_governance_tool.review_results import (
    REVIEW_RESULTS_INPUT_LIMIT, REVIEW_RESULTS_RECEIPT_LIMIT,
    ReviewResultInputError,
    decode_review_results, normalize_review_results, review_result_template,
)
from task_governance_tool.reviews import (
    REVIEW_TARGET_KINDS, ReviewEvidenceError, validate_stored_review_target,
)
from task_governance_tool.task_values import (
    SQLITE_INT64_MAX, TaskValidationError, reject_private_or_raw_content,
    validate_legacy_m19_7_stored_text, validate_text,
)
from task_governance_tool.verification_declaration import verification_requirement
from task_governance_tool.session_identity import CallerIdentity, capture_caller_identity
from task_governance_tool import review_session_transport as session_transport


PACKET_LIMIT = 32768
_PACKET_KEYS = {
    "task", "contract", "review_target", "changed_paths_available", "changed_paths",
    "changed_paths_total", "changed_paths_truncated", "review_focus", "required_output",
    "result_template", "result_instructions", "receipt_command",
}


class HandoffError(Exception):
    def __init__(self, code="handoff_invalid_input"):
        self.code = code


def _fail(code="handoff_invalid_input"):
    raise HandoffError(code)


def _identity(details, *, change_time=False):
    # Windows Python exposes different ctime meanings through lstat/fstat.
    # Compare ctime only between observations using the same API; cross-API
    # checks retain object identity, mode, links, size and nanosecond mtime.
    return (details.st_dev, details.st_ino, details.st_mode, details.st_nlink,
            details.st_size, details.st_mtime_ns) + ((details.st_ctime_ns,) if change_time else ())


def _physical(details, *, directory=False):
    if (stat.S_ISLNK(details.st_mode)
            or getattr(details, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            or not (stat.S_ISDIR if directory else stat.S_ISREG)(details.st_mode)
            or (not directory and details.st_nlink != 1)):
        _fail("handoff_path_unsafe")


def _parents(path):
    """Observe the whole existing physical ancestor chain, including repo ancestors."""
    observed = []
    for parent in reversed(path.parents):
        details = parent.lstat()
        _physical(details, directory=True)
        observed.append((parent, details.st_dev, details.st_ino))
    return observed


def _check_parents(observed):
    for parent, device, inode in observed:
        details = parent.lstat()
        _physical(details, directory=True)
        if (details.st_dev, details.st_ino) != (device, inode):
            _fail("handoff_file_changed")


def _relative_path(repo, supplied):
    # Portable project-relative names only: no ADS, device names, traversal,
    # reserved state/package areas or invisible path spellings.
    reject_private_or_raw_content("review_result_path", supplied)
    if (not supplied or len(supplied.encode("utf-8")) > 4096
            or "\\" in supplied or any(ord(c) < 32 for c in supplied)):
        _fail("handoff_path_unsafe")
    parts = supplied.split("/")
    reserved = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(10)), *(f"lpt{i}" for i in range(10))}
    if any(not p or p in {".", ".."} or p.endswith((".", " "))
           or any(c in p for c in ':*?"<>|') or p.split(".")[0].lower() in reserved for p in parts):
        _fail("handoff_path_unsafe")
    if parts[0].lower() in {".git", ".agents", ".codex", ".taskgov", "task-governance-tool"}:
        _fail("handoff_path_unsafe")
    return repo.joinpath(*parts)


def _ignored(repo, supplied):
    # --no-index is intentionally absent: tracked paths must not qualify.
    result = subprocess.run(
        [*safe_git_command(repo), "-c", "core.fsmonitor=false", "check-ignore", "--quiet", "--", supplied],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        timeout=2, shell=False, env=safe_git_environment(), check=False,
    )
    if result.returncode != 0:
        _fail("handoff_ignore_required")


def _path(repo, supplied):
    path = _relative_path(repo, supplied)
    if path.suffix.lower() != ".json":
        _fail("handoff_path_unsafe")
    _parents(path)
    _ignored(repo, supplied)
    return path


def _read(path, limit):
    parents = _parents(path)
    before = path.lstat()
    _physical(before)
    if before.st_size > limit:
        _fail("handoff_input_too_large")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        _physical(opened)
        if _identity(opened) != _identity(before):
            _fail("handoff_file_changed")
        chunks = []
        size = 0
        while size <= limit:
            chunk = os.read(descriptor, min(65536, limit + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
        after = os.fstat(descriptor)
        if size != before.st_size or _identity(after, change_time=True) != _identity(opened, change_time=True):
            _fail("handoff_file_changed")
    finally:
        os.close(descriptor)
    _check_parents(parents)
    final = path.lstat()
    _physical(final)
    if _identity(final, change_time=True) != _identity(before, change_time=True):
        _fail("handoff_file_changed")
    return b"".join(chunks)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail()
        result[key] = value
    return result


def _packet_target(value):
    if (type(value) is not dict or set(value) != {"kind", "value", "base_revision", "generation"}
            or type(value["generation"]) is not int or not 1 <= value["generation"] <= SQLITE_INT64_MAX
            or any(type(value[name]) is not str for name in ("kind", "value", "base_revision"))
            or value["kind"] not in REVIEW_TARGET_KINDS):
        _fail("handoff_response_invalid")
    for name in ("kind", "value", "base_revision"):
        validate_text("review_target_" + name, value[name], required=name != "base_revision", limit=500)
    validate_stored_review_target({"review_target_" + key: item for key, item in value.items()})


def _packet(raw, expected_task_id=None):
    """Pure complete-Packet validation shared by every handoff entry point.

    Stored compatibility is constraints-only. This neither repairs input nor
    checks live state; reviewer read and registration retain those boundaries.
    """
    if len(raw) > PACKET_LIMIT:
        _fail("handoff_input_too_large")
    packet = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique,
                        parse_constant=lambda value: _fail())
    if type(packet) is not dict or set(packet) not in (_PACKET_KEYS, _PACKET_KEYS | {"review_session_context"}):
        _fail()
    if "review_session_context" in packet:
        session_transport.validate_context(packet["review_session_context"], allow_unknown_execution=True)
    task, contract, target = packet["task"], packet["contract"], packet["review_target"]
    if (type(task) is not dict or type(contract) is not dict or type(target) is not dict
            or type(task.get("task_id")) is not str or type(task.get("review_tier")) is not int
            or task["review_tier"] not in (0, 1, 2) or type(contract.get("revision")) is not int
            or type(target.get("generation")) is not int):
        _fail()
    expected = review_result_template(task["task_id"], contract["revision"], target)
    if json.dumps(packet["result_template"], sort_keys=True) != json.dumps(expected, sort_keys=True):
        _fail()
    if (set(task) != {
            "task_id", "title", "status", "verification", "verification_not_required_reason", "review_tier"}
            or set(contract) not in ({"revision", "scope", "acceptance", "constraints"},
                                    {"revision", "scope", "acceptance", "constraints", "authority_ref"})
            or (expected_task_id is not None and task["task_id"] != expected_task_id)):
        _fail("review_target_mismatch")
    _packet_target(target)
    reason = task["verification_not_required_reason"]
    if type(reason) is not str:
        _fail("handoff_response_invalid")
    validate_text("verification_not_required_reason", reason, limit=1000)
    if not 0 <= contract["revision"] <= SQLITE_INT64_MAX:
        _fail("handoff_response_invalid")
    # Older saved Packets omit this context field. Never fill it or resolve it
    # as a path; supplied references retain the ordinary privacy/size boundary.
    if "authority_ref" in contract:
        reference = contract["authority_ref"]
        if type(reference) is not str or "\n" in reference or "\r" in reference:
            _fail("handoff_response_invalid")
        validate_text("contract_authority_ref", reference, limit=500)
    for owner, names in ((task, ("title", "status", "verification")),
                         (contract, ("scope", "acceptance", "constraints"))):
        for name in names:
            if type(owner[name]) is not str:
                _fail("handoff_response_invalid")
            if owner is contract:
                validator = validate_legacy_m19_7_stored_text if name == "constraints" else validate_text
                validator("contract_" + name, owner[name])
            else:
                validate_text(name, owner[name])
    verification_requirement(task["verification"], reason)
    for name in ("review_focus", "required_output", "result_instructions"):
        if (type(packet[name]) is not list or not packet[name]
                or any(type(item) is not str or not item for item in packet[name])):
            _fail("handoff_response_invalid")
    if type(packet["receipt_command"]) is not str or not packet["receipt_command"]:
        _fail("handoff_response_invalid")
    if (type(packet["changed_paths"]) is not list
            or any(type(item) is not str for item in packet["changed_paths"])
            or type(packet["changed_paths_available"]) is not bool
            or type(packet["changed_paths_truncated"]) is not bool
            or type(packet["changed_paths_total"]) is not int
            or packet["changed_paths_total"] < len(packet["changed_paths"])
            or (not packet["changed_paths_truncated"] and packet["changed_paths_total"] != len(packet["changed_paths"]))):
        _fail("handoff_response_invalid")
    return packet


def _validate(raw, packet, approvals, *, original=False):
    payload = decode_review_results(raw)
    normalize_review_results(payload, review_tier=packet["task"]["review_tier"],
                             user_approved_reviewers=approvals)
    if original and (not raw.lstrip().startswith(b"{") or len(payload["receipts"]) != 1):
        _fail()
    expected = packet["result_template"]
    if any(payload[key] != expected[key] for key in ("version", "task_id", "contract_revision", "review_target")):
        _fail("review_target_mismatch")
    return payload


def _write_new(path, raw):
    parents = _parents(path)
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        _physical(os.fstat(descriptor))
        _check_parents(parents)
        offset = 0
        while offset < len(raw):
            written = os.write(descriptor, raw[offset:])
            if written <= 0:
                _fail("handoff_io_failed")
            offset += written
        os.fsync(descriptor)
        created = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    _check_parents(parents)
    if _identity(path.lstat()) != _identity(created):
        _fail("handoff_file_changed")
    # Never remove incomplete or uncertain originals on failure.


def save(repo, packet_path, output, raw, approvals=()):
    packet_file = _path(repo, packet_path)
    packet_raw = _read(packet_file, PACKET_LIMIT)
    packet = _packet(packet_raw)
    payload = _validate(raw, packet, approvals, original=True)
    binding_path, binding_raw = None, None
    if "review_session_context" in packet:
        metadata = session_transport.metadata_for(packet, raw, capture_caller_identity())
        session_transport.validate_metadata(metadata, raw)
        binding_path = _path(repo, output + ".session.json")
        binding_raw = json.dumps(metadata, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    destination = _path(repo, output)
    _write_new(destination, raw)
    if binding_path is not None:
        _write_new(binding_path, binding_raw)
    saved = _read(destination, REVIEW_RESULTS_INPUT_LIMIT)
    _validate(saved, packet, approvals, original=True)
    if saved != raw or _read(packet_file, PACKET_LIMIT) != packet_raw:
        _fail("handoff_file_changed")
    if binding_path is not None and _read(binding_path, PACKET_LIMIT) != binding_raw:
        _fail("handoff_file_changed")
    entry = payload["receipts"][0]
    result = {"ok": True, "status": "saved", "path": output,
              "verdict": entry["verdict"], "finding_count": len(entry["findings"])}
    if binding_raw is not None:
        result["review_session"] = metadata
        from task_governance_tool.usage_lifecycle import register_reviewer
        register_reviewer(repo, CallerIdentity(metadata["session_id"]))
    return result


def submission(repo, packet_path, originals, approvals=()):
    """Confirm the complete saved handoff before any registration dispatch.

    The caller need not separately retrieve save acknowledgements or originals.
    This observes the files now; it does not infer a past save/child outcome.
    """
    if not 1 <= len(originals) <= REVIEW_RESULTS_RECEIPT_LIMIT:
        _fail()
    packet_file = _path(repo, packet_path)
    packet_raw = _read(packet_file, PACKET_LIMIT)
    packet = _packet(packet_raw)
    paths = [_path(repo, item) for item in originals]
    if len(set(os.path.normcase(str(path)) for path in paths)) != len(paths):
        _fail()
    documents = [_read(path, REVIEW_RESULTS_INPUT_LIMIT) for path in paths]
    if any(not raw.lstrip().startswith(b"{") for raw in documents):
        _fail()
    framed = b"[" + b",".join(documents) + b"]"
    payload = _validate(framed, packet, approvals)
    binding_files = [(_path if "review_session_context" in packet else _relative_path)(
        repo, item + ".session.json") for item in originals]
    binding_documents = []
    if "review_session_context" in packet:
        for path, raw in zip(binding_files, documents):
            binding_raw = _read(path, PACKET_LIMIT)
            metadata = json.loads(binding_raw.decode("utf-8"), object_pairs_hook=_unique)
            session_transport.validate_metadata(metadata, raw)
            context = {key: metadata[key] for key in ("version", "project_id", "execution_id")}
            if context != packet["review_session_context"]:
                _fail("review_target_mismatch")
            binding_documents.append((binding_raw, metadata))
        framed = session_transport.frame_submission(documents, [metadata for _, metadata in binding_documents])
    elif any(os.path.lexists(path) for path in binding_files):
        _fail("handoff_invalid_input")
    if _read(packet_file, PACKET_LIMIT) != packet_raw:
        _fail("handoff_file_changed")
    for path, raw in zip(paths, documents):
        if _read(path, REVIEW_RESULTS_INPUT_LIMIT) != raw:
            _fail("handoff_file_changed")
    for path, (raw, _) in zip(binding_files, binding_documents):
        if _read(path, PACKET_LIMIT) != raw:
            _fail("handoff_file_changed")
    return payload["task_id"], framed


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        _fail("handoff_invalid_arguments")


def _emit(value, *, utf8=False):
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


def main(argv=None):
    registration_status = None
    try:
        from task_governance_tool.review_handoff_preparation import (
            add_parser, add_material_parser, prepare, read_for_reviewer, read_material,
        )
        parser = _Parser(description=__doc__)
        commands = parser.add_subparsers(dest="operation", required=True)
        add_parser(commands)
        add_material_parser(commands)
        reader = commands.add_parser("read", help="Display the saved Packet for an explicitly assigned independent reviewer")
        reader.add_argument("--repo", required=True)
        reader.add_argument("--packet", required=True)
        reader.add_argument("--material-details", action="store_true",
                            help="Show conditional individual retrieval, discovery and recovery guidance")
        ended = commands.add_parser("wait-ended", help="Declare the bound supervisor's all-ended decision for optional numerical usage")
        ended.add_argument("--repo", required=True)
        packet_source = ended.add_mutually_exclusive_group(required=True)
        packet_source.add_argument("--packet", help="Ignored project-relative complete Packet JSON")
        packet_source.add_argument("--from-stdin", action="store_true", help="Complete actual Packet as bounded UTF-8 stdin")
        basis = commands.add_parser("wait-basis", help="Read the original Task's current structural wait basis without writes")
        basis.add_argument("--repo", required=True)
        basis.add_argument("--task-id", required=True)
        for operation in ("save", "submit"):
            command = commands.add_parser(operation, help=(
                "Confirm complete saved originals and register once; no prior acknowledgement read required"
                if operation == "submit" else "Save one original with physical readback confirmation"))
            command.add_argument("--repo", required=True, help="Explicit governed project root")
            command.add_argument("--packet", required=True, help="Ignored project-relative complete Packet JSON")
            command.add_argument("--user-approved-reviewer", action="append", default=[])
            if operation == "save":
                command.add_argument("--output", required=True, help="Unused ignored project-relative JSON path; parent must exist")
            else:
                command.add_argument("originals", nargs="+", help="Ignored project-relative original JSON paths")
        args = parser.parse_args(argv)
        if args.operation == "submit":
            registration_status = "not_started"
        repo = Path(os.path.abspath(args.repo))
        if args.operation == "material":
            return read_material(repo, args)
        if args.operation == "prepare":
            result = prepare(repo, args)
            return (0 if result["ok"] else 1) if _emit(result) else 1
        if args.operation == "read":
            return 0 if _emit(read_for_reviewer(repo, args.packet, material_details=args.material_details), utf8=True) else 1
        if args.operation == "wait-ended":
            from task_governance_tool.review_wait import wait_ended
            raw = sys.stdin.buffer.read(PACKET_LIMIT + 1) if args.from_stdin else None
            result = wait_ended(repo, packet_path=args.packet, raw=raw)
            return (0 if result["ok"] else 1) if _emit(result) else 1
        if args.operation == "wait-basis":
            from task_governance_tool.review_wait_basis import wait_basis
            result = wait_basis(repo, task_id=args.task_id)
            return (0 if result["ok"] else 1) if _emit(result) else 1
        if args.operation == "save":
            raw = sys.stdin.buffer.read(REVIEW_RESULTS_INPUT_LIMIT + 1)
            result = save(repo, args.packet, args.output, raw, args.user_approved_reviewer)
            return 0 if _emit(result) else 1
        task_id, framed = submission(repo, args.packet, args.originals, args.user_approved_reviewer)
        entrypoint = Path(__file__).parent.parent / "taskgov.py"
        command = [sys.executable, "-B", str(entrypoint), "review", "result", "add", task_id,
                   "--repo", str(repo), "--json"]
        for reviewer in args.user_approved_reviewer:
            command.append("--user-approved-reviewer=" + reviewer)
        # No shell pipe, timeout retry, reserialization, alternate writer or SQL.
        # From dispatch onward a local exception cannot prove rollback. The
        # child's own normal response (including rejection/exit 2) passes through.
        registration_status = "unknown"
        return subprocess.run(command, input=framed, check=False, shell=False).returncode
    except ReviewResultInputError as exc:
        code, field, reason = exc.code, exc.field, exc.message
    except (HandoffError, ReviewEvidenceError, TaskValidationError) as exc:
        code = exc.code
    except FileNotFoundError:
        code = "handoff_input_missing" if registration_status == "not_started" else "handoff_io_or_input_failed"
    except (OSError, ValueError, KeyError, TypeError, RecursionError, subprocess.SubprocessError):
        code = "handoff_io_or_input_failed"
    except KeyboardInterrupt:
        code = "handoff_outcome_unknown"
    if "args" in locals() and args.operation == "material":
        # A failed stream may have delivered a prefix. Never append a JSON
        # envelope to raw Git material or label that prefix complete.
        try:
            print("Review material failed: " + code, file=sys.stderr, flush=True)
        except OSError:
            pass
        return 1
    message = "Review handoff failed; preserve originals and inspect the outcome before retry."
    if registration_status == "not_started":
        message = "Registration was not started; preserve originals and recover the reported handoff input before submitting."
    elif registration_status == "unknown":
        message = "Registration outcome is unknown; preserve originals and inspect public recorded evidence before any retry."
    failure = {"ok": False, "code": code, "message": message}
    if "field" in locals():
        suffix = message if registration_status is not None else "preserve originals and inspect the outcome before retry."
        failure.update(field=field, message=reason + "; " + suffix)
    if registration_status is not None:
        failure["registration_status"] = registration_status
    _emit(failure)
    return 1
