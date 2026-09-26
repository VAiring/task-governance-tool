"""Capture one of three existing CLI operations, then prepare caller-owned handoff.

No arbitrary command runner, live-state reader, reviewer launcher or ledger.
Source mutations and transport failures have separate outcomes; neither is retried.
Only a complete Packet is persisted, never the captured response or diagnostics.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

from task_governance_tool import review_handoff as files
from task_governance_tool.task_values import (
    SQLITE_INT64_MAX, validate_legacy_m19_7_stored_text, validate_task_id, validate_text,
)
from task_governance_tool.reviews import REVIEW_TARGET_KINDS, validate_stored_review_target
from task_governance_tool.verification_results import (
    VERIFICATION_RESULT_INPUT_LIMIT, decode_verification_result,
)
from task_governance_tool.verification_receipts import VerificationReceiptError

RESPONSE_LIMIT = 262144
COMMANDS = {"target": "review.target.set", "receipt": "verification.receipt.add",
            "recover": "review.prepare"}


def add_parser(commands):
    parser = commands.add_parser("prepare", help="Run one explicit Packet-producing CLI operation and prepare handoff")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--directory", required=True, help="Unused ignored project-relative directory; missing parents may be created")
    parser.add_argument("--reviewers", type=int, choices=range(1, 9), default=2)
    actions = parser.add_subparsers(dest="source_operation", required=True)
    target = actions.add_parser("target")
    target.add_argument("task_id")
    target.add_argument("--kind", required=True, choices=("git_snapshot", "git_commit", "diff_fingerprint", "external_revision"))
    target.add_argument("--revision")
    receipt = actions.add_parser("receipt")
    receipt.add_argument("task_id")
    receipt.add_argument("--from-stdin", action="store_true")
    for name in ("result", "duration-ms", "scope-coverage", "expected-target-generation"):
        receipt.add_argument("--" + name)
    recover = actions.add_parser("recover")
    recover.add_argument("task_id")
    binding = recover.add_mutually_exclusive_group(required=True)
    binding.add_argument("--expected-binding")
    binding.add_argument("--verification-receipt-id")


def _command(repo, args):
    action = args.source_operation
    task_id = args.task_id
    arguments = {"target": ["review", "target", "set"],
                 "receipt": ["verification", "receipt", "add"],
                 "recover": ["review", "prepare"]}[action] + [task_id]
    names = {"target": ("kind", "revision"), "recover": ("expected_binding", "verification_receipt_id"),
             "receipt": ("result", "duration_ms", "scope_coverage", "expected_target_generation")}[action]
    for name in names:
        value = getattr(args, name, None)
        if value is not None:
            arguments.append("--" + name.replace("_", "-") + "=" + value)
    raw = None
    if action == "receipt" and args.from_stdin:
        if any(getattr(args, name) is not None for name in names):
            files._fail("handoff_invalid_arguments")
        raw = sys.stdin.buffer.read(VERIFICATION_RESULT_INPUT_LIMIT + 1)
        decode_verification_result(raw, task_id=task_id)
        arguments.append("--from-stdin")
    entry = Path(__file__).parent.parent / "taskgov.py"
    return [sys.executable, "-B", str(entry), *arguments, "--repo", str(repo), "--json"], raw


def _directory(repo, supplied):
    destination = files._relative_path(repo, supplied)
    missing = []
    current = destination
    while True:
        try:
            details = current.lstat()
        except FileNotFoundError:
            missing.append(current)
            current = current.parent
        else:
            files._physical(details, directory=True)
            break
    if not missing:
        files._fail("handoff_destination_exists")
    observed = files._parents(current / "unused.json")
    for path in missing:
        files._ignored(repo, path.relative_to(repo).as_posix() + "/")
    # Check actual future file names too: an ignore negation may differ from its directory.
    return destination, list(reversed(missing)), observed


def _capture(command, raw, started):
    process = subprocess.Popen(command, stdin=subprocess.PIPE if raw is not None else subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, shell=False)
    started()
    try:
        if raw is not None:
            process.stdin.write(raw)
            process.stdin.close()
        captured = bytearray()
        oversized = False
        while True:
            chunk = process.stdout.read(65536)
            if not chunk:
                break
            if len(captured) + len(chunk) > RESPONSE_LIMIT:
                oversized = True
            elif not oversized:
                captured.extend(chunk)
        # Drain without retaining excess output; never kill/replay a possibly committed writer.
        return process.wait(), None if oversized else bytes(captured)
    finally:
        process.stdout.close()
        if process.stdin is not None and not process.stdin.closed:
            process.stdin.close()


def _json(raw):
    return json.loads(raw.decode("utf-8"), object_pairs_hook=files._unique,
                      parse_constant=lambda value: files._fail("handoff_response_invalid"))


def _messages(value):
    if type(value) is not list:
        files._fail("handoff_response_invalid")
    for row in value:
        if type(row) is not dict or set(row) != {"code", "message"}:
            files._fail("handoff_response_invalid")
        for key in row:
            if type(row[key]) is not str:
                files._fail("handoff_response_invalid")
            files.reject_private_or_raw_content(key, row[key])
    return value


def _envelope(raw, code, command):
    value = _json(raw)
    if (type(value) is not dict or set(value) != {"ok", "command", "project_id", "data", "warnings", "errors"}
            or type(value["ok"]) is not bool or type(value["data"]) is not dict
            or value["command"] not in ({command} if value["ok"] else {command, "parse"})
            or (value["ok"] and code != 0) or (not value["ok"] and code not in (1, 2))):
        files._fail("handoff_response_invalid")
    _messages(value["warnings"])
    _messages(value["errors"])
    if bool(value["errors"]) == value["ok"]:
        files._fail("handoff_response_invalid")
    return value


def _target(value):
    if (type(value) is not dict or set(value) != {"kind", "value", "base_revision", "generation"}
            or type(value["generation"]) is not int or not 1 <= value["generation"] <= SQLITE_INT64_MAX
            or any(type(value[name]) is not str for name in ("kind", "value", "base_revision"))
            or value["kind"] not in REVIEW_TARGET_KINDS):
        files._fail("handoff_response_invalid")
    for name in ("kind", "value", "base_revision"):
        validate_text("review_target_" + name, value[name], required=name != "base_revision", limit=500)
    validate_stored_review_target({"review_target_" + key: item for key, item in value.items()})


def _packet(value, task_id):
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(raw) > files.PACKET_LIMIT:
        files._fail("handoff_input_too_large")
    packet = files._packet(raw)
    if (set(packet["task"]) != {"task_id", "title", "status", "verification", "review_tier"}
            or set(packet["contract"]) != {"revision", "scope", "acceptance", "constraints"}
            or set(packet["review_target"]) != {"kind", "value", "base_revision", "generation"}
            or packet["task"]["task_id"] != task_id):
        files._fail("review_target_mismatch")
    _target(packet["review_target"])
    if not 0 <= packet["contract"]["revision"] <= SQLITE_INT64_MAX:
        files._fail("handoff_response_invalid")
    for owner, names in ((packet["task"], ("title", "status", "verification")),
                         (packet["contract"], ("scope", "acceptance", "constraints"))):
        for name in names:
            if type(owner[name]) is not str:
                files._fail("handoff_response_invalid")
            if owner is packet["contract"]:
                # This is the public stored-Contract projection, not new caller
                # input. Preserve its existing constraints-only compatibility.
                validator = validate_legacy_m19_7_stored_text if name == "constraints" else validate_text
                validator("contract_" + name, owner[name])
            else:
                validate_text(name, owner[name])
    for name in ("review_focus", "required_output", "result_instructions"):
        if (type(packet[name]) is not list or not packet[name]
                or any(type(item) is not str or not item for item in packet[name])):
            files._fail("handoff_response_invalid")
    if type(packet["receipt_command"]) is not str or not packet["receipt_command"]:
        files._fail("handoff_response_invalid")
    if (type(packet["changed_paths"]) is not list
            or any(type(item) is not str for item in packet["changed_paths"])
            or type(packet["changed_paths_available"]) is not bool
            or type(packet["changed_paths_truncated"]) is not bool
            or type(packet["changed_paths_total"]) is not int
            or packet["changed_paths_total"] < len(packet["changed_paths"])
            or (not packet["changed_paths_truncated"] and packet["changed_paths_total"] != len(packet["changed_paths"]))):
        files._fail("handoff_response_invalid")
    return raw


def _source(data, args):
    action = args.source_operation
    if action == "recover":
        return {}, {"status": "ready", "packet": data, "errors": []}
    preparation = data["review_preparation"]
    if action == "target":
        task = data["task"]
        source = {"task_id": task["task_id"], "review_target": {
            name: task["review_target_" + name] for name in ("kind", "value", "base_revision", "generation")},
            "verification_route": data["verification_route"], "blocking_code": data["blocking_code"],
            "preparation_binding": preparation["binding"]}
    else:
        receipt = data["receipt"]
        source = {"task_id": receipt["task_id"], "contract_revision": receipt["contract_revision"],
                  "review_target": dict(receipt["source_revision"]), "verification_receipt_id": receipt["verification_receipt_id"],
                  "result": receipt["result"], "scope_coverage": receipt["scope_coverage"]}
        # The existing Receipt projection uses null for an absent non-snapshot
        # base; Packet/Task projections use the empty string for that same fact.
        if source["review_target"]["kind"] != "git_snapshot" and source["review_target"]["base_revision"] is None:
            source["review_target"]["base_revision"] = ""
    if source["task_id"] != args.task_id:
        files._fail("review_target_mismatch")
    _target(source["review_target"])
    if action == "target":
        route = source["verification_route"]
        if (route not in ("not_required", "runner_pass", "receipt_required", "blocked")
                or source["blocking_code"] != ("verification_receipt_blocking" if route == "blocked" else None)
                or (route in ("not_required", "runner_pass")) != (preparation["status"] in ("ready", "failed"))):
            files._fail("handoff_response_invalid")
    elif ((source["result"] == "pass" and source["scope_coverage"] == "full")
          != (preparation["status"] in ("ready", "failed"))):
        files._fail("handoff_response_invalid")
    return source, preparation


def _shell(arguments):
    if os.name == "nt":
        return "& " + " ".join("'" + value.replace("'", "''") + "'" for value in arguments)
    return shlex.join(arguments)


def _requests(repo, args, packet_path):
    entry = str(Path(__file__).parent.parent / "review_handoff.py")
    base = [sys.executable, "-B", entry]
    common = ["--repo=" + str(repo), "--packet=" + packet_path]
    reviewers = []
    paths = [args.directory + f"/review-{index}.json" for index in range(1, args.reviewers + 1)]
    for path in paths:
        save = _shell([*base, "save", *common, "--output=" + path])
        if os.name == "nt":
            invocation = "$OutputEncoding = [Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)\n@'\n<completed original JSON>\n'@ | " + save
        else:
            invocation = save + " <<'TASKGOV_REVIEW_RESULT'\n<completed original JSON>\nTASKGOV_REVIEW_RESULT"
        reviewers.append({"result_path": path, "save_command": save, "request": (
            "Independently review the complete exact target under current project authority. Read the complete Packet at "
            + str(repo / packet_path) + ". Do not omit required source or governing-document inspection. "
            "Complete its result_template with actual judgment, provenance and all Findings. "
            "Replace only the JSON placeholder below; run this fixed save operation, not new save/validation code.\n"
            + invocation + "\nOn saved acknowledgement return path, verdict and Finding count once in the final response; "
            "do not echo JSON or send a duplicate normal-success notification. Report problems/questions when needed. "
            "Preserve failed residue; do not overwrite, resubmit or claim unknown outcomes as success."
        )})
    return {"status": "ready", "packet_path": packet_path, "review_requests": reviewers,
            "submit_command": _shell([*base, "submit", *common, "--", *paths])}


def prepare(repo, args):
    result = {"ok": False, "command": COMMANDS[args.source_operation], "operation_status": "not_started",
              "source_exit_code": None, "source": None, "warnings": [], "errors": [],
              "handoff": {"status": "unavailable"}}
    try:
        args.task_id = validate_task_id(args.task_id)
        command, raw = _command(repo, args)
        destination, missing, observed = _directory(repo, args.directory)
        packet_path = args.directory + "/packet.json"
        result_paths = [args.directory + f"/review-{index}.json" for index in range(1, args.reviewers + 1)]
        for path in (packet_path, *result_paths):
            files._ignored(repo, path)
        code, response = _capture(command, raw, lambda: result.update(operation_status="unknown"))
        result["source_exit_code"] = code
        envelope = _envelope(response, code, result["command"])
        result.update(operation_status="succeeded" if envelope["ok"] else "failed",
                      warnings=envelope["warnings"], errors=envelope["errors"])
        if not envelope["ok"]:
            return result
        source, preparation = _source(envelope["data"], args)
        result["source"] = source
        status = preparation["status"]
        if status not in ("ready", "failed", "blocked", "not_applicable"):
            files._fail("handoff_response_invalid")
        errors = _messages(preparation["errors"])
        if status != "ready":
            if preparation["packet"] is not None:
                files._fail("handoff_response_invalid")
            result.update(ok=status == "not_applicable", errors=errors, handoff={"status": status})
            return result
        if errors:
            files._fail("handoff_response_invalid")
        packet = preparation["packet"]
        packet_bytes = _packet(packet, args.task_id)
        if source and (source["review_target"] != packet["review_target"]
                       or source.get("contract_revision", packet["contract"]["revision"]) != packet["contract"]["revision"]):
            files._fail("review_target_mismatch")
        # Recheck before the first write; no path created by another caller is adopted.
        files._check_parents(observed)
        for path in missing:
            parents = files._parents(path)
            files._ignored(repo, path.relative_to(repo).as_posix() + "/")
            path.mkdir(mode=0o700)
            files._physical(path.lstat(), directory=True)
            files._check_parents(parents)
        parents = files._parents(destination / "packet.json")
        target = files._path(repo, packet_path)
        files._write_new(target, packet_bytes)
        saved = files._read(target, files.PACKET_LIMIT)
        if saved != packet_bytes or _packet(_json(saved), args.task_id) != packet_bytes:
            files._fail("handoff_file_changed")
        for path in result_paths:
            target_result = files._path(repo, path)
            if os.path.lexists(target_result):
                files._fail("handoff_destination_exists")
        files._check_parents(parents)
        result.update(ok=True, handoff=_requests(repo, args, packet_path))
        return result
    except (files.HandoffError, files.ReviewEvidenceError, files.TaskValidationError, VerificationReceiptError) as exc:
        code = exc.code
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RecursionError, subprocess.SubprocessError):
        code = "handoff_io_or_input_failed"
    except KeyboardInterrupt:
        code = "handoff_outcome_unknown"
    result.update(ok=False, handoff={"status": "failed"}, errors=[{
        "code": code, "message": "Handoff preparation failed; preserve state and residue, inspect the source outcome before retry."
    }])
    return result
