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
import subprocess
import sys
from pathlib import Path

from . import review_handoff_transport as files, review_handoff_originals as originals
from . import session_identity
from .review_results import REVIEW_RESULTS_INPUT_LIMIT, ReviewResultInputError
from .review_answer import AnswerError
from .reviews import ReviewEvidenceError
from .task_values import TaskValidationError
from .session_identity import CallerIdentity
from . import review_session_transport as session_transport


def save(repo, packet_path, output, raw, approvals=()):
    packet_file = files.checked_path(repo, packet_path)
    packet_raw = files.read_bounded(packet_file, files.PACKET_LIMIT)
    packet = files.decode_packet(packet_raw)
    from task_governance_tool.review_preparation import save_admission
    caller = session_identity.capture_caller_identity()
    with save_admission(repo, packet_path, packet_raw, packet, output, caller):
        if files.read_bounded(packet_file, files.PACKET_LIMIT) != packet_raw:
            files.fail("handoff_file_changed")
        return _save_admitted(repo, packet_path, output, raw, approvals, caller, packet_file, packet_raw, packet)


def _save_admitted(repo, packet_path, output, raw, approvals, caller, packet_file, packet_raw, packet):
    payload = files.validate_results(raw, packet, approvals, original=True)
    binding_path, binding_raw = None, None
    if "review_session_context" in packet:
        metadata = session_transport.metadata_for(packet, raw, caller)
        session_transport.validate_metadata(metadata, raw)
        binding_path = files.checked_path(repo, output + ".session.json")
        binding_raw = json.dumps(metadata, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    destination = files.checked_path(repo, output)
    files.write_exclusive(destination, raw)
    if binding_path is not None:
        files.write_exclusive(binding_path, binding_raw)
    saved = files.read_bounded(destination, REVIEW_RESULTS_INPUT_LIMIT)
    files.validate_results(saved, packet, approvals, original=True)
    if saved != raw or files.read_bounded(packet_file, files.PACKET_LIMIT) != packet_raw:
        files.fail("handoff_file_changed")
    if binding_path is not None and files.read_bounded(binding_path, files.PACKET_LIMIT) != binding_raw:
        files.fail("handoff_file_changed")
    entry = payload["receipts"][0]
    result = {"ok": True, "status": "saved", "path": output,
              "verdict": entry["verdict"], "finding_count": len(entry["findings"])}
    if binding_raw is not None:
        result["review_session"] = metadata
        from task_governance_tool.usage_lifecycle import register_reviewer
        register_reviewer(repo, CallerIdentity(metadata["session_id"]))
    return result


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        files.fail("handoff_invalid_arguments")


def main(argv=None):
    raw_argv = tuple(argv if argv is not None else sys.argv[1:])
    registration_status = None
    try:
        from task_governance_tool.review_handoff_preparation import (
            add_parser, add_material_parser, prepare, read_for_reviewer, read_material,
        )
        parser = _Parser(description=__doc__)
        parser.add_argument("--records-only", action="store_true",
                            help="restrict to ordinary record transport without Runner or integrated finalization")
        commands = parser.add_subparsers(dest="operation", required=True)
        add_parser(commands)
        add_parser(commands, "prepare-finalization")
        from task_governance_tool import review_handoff_continuation
        review_handoff_continuation.add_parser(commands)
        add_material_parser(commands)
        workspace = commands.add_parser("workspace", help="Prepare or clean one optional fixed-target verification workspace; never execute tests")
        workspace.add_argument("workspace_operation", choices=("prepare", "cleanup"))
        workspace.add_argument("--repo", required=True)
        workspace.add_argument("--packet", required=True)
        workspace.add_argument("--packet-sha256", required=True)
        workspace.add_argument("--workspace-id", required=True)
        workspace.add_argument("--temp-root-sha256", required=True)
        finalize = commands.add_parser("finalize", help="Run only the retained authorized finalization; preserve successful stages")
        finalize.add_argument("--repo", required=True)
        finalize.add_argument("--task-id", required=True)
        finalize.add_argument("--target-generation", type=int, help="Retained target generation for explicit recovery; never recaptures material")
        finalize.add_argument("--check", action="store_true", help="Read retained stages and available evidence without any mutation")
        reader = commands.add_parser("read", help="Display the saved Packet for an explicitly assigned independent reviewer")
        reader.add_argument("--repo", required=True)
        reader.add_argument("--packet", required=True)
        reader.add_argument("--material-details", action="store_true",
                            help="Show conditional retrieval, discovery, recovery and optional verification workspace details")
        reader.add_argument("--section", choices=("all", "context", "material", "verification", "workspace"), default="all")
        reader.add_argument("--start", type=int, default=0)
        reader.add_argument("--content-sha256")
        reader.add_argument("--packet-sha256")
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
        args = parser.parse_args(raw_argv)
        if args.records_only:
            from task_governance_tool.task_record_policy import RecordPolicyError, validate_record_arguments
            try:
                validate_record_arguments("review_handoff.py", raw_argv)
            except RecordPolicyError:
                files.fail("record_operation_not_allowed")
        if args.operation == "submit":
            registration_status = "not_started"
        repo = Path(os.path.abspath(args.repo))
        if args.operation == "continue":
            from task_governance_tool.review_continuation import INPUT_LIMIT
            result = review_handoff_continuation.continue_reviews(repo, args, sys.stdin.buffer.read(INPUT_LIMIT + 1))
            return (0 if result["ok"] else 1) if files.emit_json(result) else 1
        if args.operation == "workspace":
            from task_governance_tool.review_workspace import operate
            result = operate(repo, args)
            return (0 if result["ok"] else 1) if files.emit_json(result, utf8=True) else 1
        if args.operation == "material":
            return read_material(repo, args)
        if args.operation in {"prepare", "prepare-finalization"}:
            result = prepare(repo, args)
            return (0 if result["ok"] else 1) if files.emit_json(result) else 1
        if args.operation == "finalize":
            from task_governance_tool.review_finalization import Finalizer, _code
            try:
                result = Finalizer(repo, args.task_id, session_identity.capture_caller_identity(), generation=args.target_generation).execute(check=args.check)
            except Exception as exc:
                result = {"ok": False, "status": "unavailable", "code": _code(exc),
                          "message": "Finalization unavailable; preserve existing state and do not replay unknown effects."}
            return (0 if result["ok"] else 1) if files.emit_json(result, utf8=True) else 1
        if args.operation == "read":
            return 0 if files.emit_json(read_for_reviewer(repo, args.packet, material_details=args.material_details,
                records_only=args.records_only, section=args.section, start=args.start,
                content_sha256=args.content_sha256, packet_sha256=args.packet_sha256), utf8=True) else 1
        if args.operation == "wait-ended":
            from task_governance_tool.review_wait import wait_ended
            raw = sys.stdin.buffer.read(files.PACKET_LIMIT + 1) if args.from_stdin else None
            result = wait_ended(repo, packet_path=args.packet, raw=raw)
            return (0 if result["ok"] else 1) if files.emit_json(result) else 1
        if args.operation == "wait-basis":
            from task_governance_tool.review_wait_basis import wait_basis
            result = wait_basis(repo, task_id=args.task_id)
            return (0 if result["ok"] else 1) if files.emit_json(result) else 1
        if args.operation == "save":
            raw = sys.stdin.buffer.read(REVIEW_RESULTS_INPUT_LIMIT + 1)
            result = save(repo, args.packet, args.output, raw, args.user_approved_reviewer)
            return 0 if files.emit_json(result) else 1
        task_id, framed = originals.submission(repo, args.packet, args.originals, args.user_approved_reviewer)
        entrypoint = Path(__file__).parent.parent / "taskgov.py"
        command = [sys.executable, "-B", str(entrypoint), "review", "result", "add", task_id,
                   "--repo", str(repo), "--json"]
        if args.records_only:
            command = [sys.executable, "-I", "-S", "-B", str(entrypoint), "--records-only", *command[3:]]
        for reviewer in args.user_approved_reviewer:
            command.append("--user-approved-reviewer=" + reviewer)
        # No shell pipe, timeout retry, reserialization, alternate writer or SQL.
        # From dispatch onward a local exception cannot prove rollback. The
        # child's own normal response (including rejection/exit 2) passes through.
        registration_status = "unknown"
        return subprocess.run(command, input=framed, check=False, shell=False).returncode
    except AnswerError as exc:
        from .review_answer_transport import answer_input_error
        sanitized = answer_input_error(exc)
        code, field, reason = sanitized.code, sanitized.field, sanitized.message
    except ReviewResultInputError as exc:
        code, field, reason = exc.code, exc.field, exc.message
    except (files.HandoffError, ReviewEvidenceError, TaskValidationError) as exc:
        code = exc.code
    except FileNotFoundError:
        code = "handoff_input_missing" if registration_status == "not_started" else "handoff_io_or_input_failed"
    except (OSError, ValueError, KeyError, TypeError, RecursionError, subprocess.SubprocessError):
        code = "handoff_io_or_input_failed"
    except KeyboardInterrupt:
        code = "handoff_outcome_unknown"
    if "args" in locals() and args.operation == "material":
        files.emit_json({"ok": False, "status": "incomplete", "code": code,
               "message": "Material unavailable; preserve complete matching siblings and recover only the missing fixed-target material. Do not treat this as PASS."}, utf8=True)
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
    files.emit_json(failure)
    return 1
