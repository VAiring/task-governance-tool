"""Capture one of three existing CLI operations, then prepare caller-owned handoff.

No arbitrary command runner, direct state access, reviewer launcher or ledger.
Source mutations and transport failures have separate outcomes; neither is retried.
Only a complete Packet is persisted, never the captured response or diagnostics.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

from task_governance_tool import review_handoff_transport as files
from task_governance_tool import session_identity
from task_governance_tool.reviews import ReviewEvidenceError
from task_governance_tool.task_values import TaskValidationError, reject_private_or_raw_content, validate_task_id
from task_governance_tool.verification_results import (
    VERIFICATION_RESULT_INPUT_LIMIT, decode_verification_result,
)
from task_governance_tool.verification_receipts import VerificationReceiptError

RESPONSE_LIMIT = 262144
INVENTORY_ENTRY_LIMIT = 20
INVENTORY_BYTE_LIMIT = 4096
COMMANDS = {"target": "review.target.set", "receipt": "verification.receipt.add",
            "recover": "review.prepare"}


def add_parser(commands, name="prepare"):
    parser = commands.add_parser(name, help=("Prepare authorized fixed-target registration, local commit and completion"
        if name == "prepare-finalization" else "Run one explicit Packet-producing CLI operation and prepare handoff"))
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
            files.fail("handoff_invalid_arguments")
        raw = sys.stdin.buffer.read(VERIFICATION_RESULT_INPUT_LIMIT + 1)
        decode_verification_result(raw, task_id=task_id)
        arguments.append("--from-stdin")
    entry = Path(__file__).parent.parent / "taskgov.py"
    if getattr(args, "records_only", False) is True:
        return [sys.executable, "-I", "-S", "-B", str(entry), "--records-only", *arguments, "--repo", str(repo), "--json"], raw
    return [sys.executable, "-B", str(entry), *arguments, "--repo", str(repo), "--json"], raw


def _directory(repo, supplied):
    destination = files.relative_path(repo, supplied)
    missing = []
    current = destination
    while True:
        try:
            details = current.lstat()
        except FileNotFoundError:
            missing.append(current)
            current = current.parent
        else:
            files.require_physical(details, directory=True)
            break
    if not missing:
        files.fail("handoff_destination_exists")
    observed = files.observe_parents(current / "unused.json")
    for path in missing:
        files.require_ignored(repo, path.relative_to(repo).as_posix() + "/")
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
    return json.loads(raw.decode("utf-8"), object_pairs_hook=files.unique_object,
                      parse_constant=lambda value: files.fail("handoff_response_invalid"))


def _messages(value):
    if type(value) is not list:
        files.fail("handoff_response_invalid")
    for row in value:
        if type(row) is not dict or set(row) != {"code", "message"}:
            files.fail("handoff_response_invalid")
        for key in row:
            if type(row[key]) is not str:
                files.fail("handoff_response_invalid")
            reject_private_or_raw_content(key, row[key])
    return value


def _envelope(raw, code, command):
    if not isinstance(raw, bytes):
        files.fail("handoff_response_invalid")
    value = _json(raw)
    if (type(value) is not dict or set(value) != {"ok", "command", "project_id", "data", "warnings", "errors"}
            or type(value["ok"]) is not bool or type(value["data"]) is not dict
            or value["command"] not in ({command} if value["ok"] else {command, "parse"})
            or (value["ok"] and code != 0) or (not value["ok"] and code not in (1, 2))):
        files.fail("handoff_response_invalid")
    _messages(value["warnings"])
    _messages(value["errors"])
    if bool(value["errors"]) == value["ok"]:
        files.fail("handoff_response_invalid")
    return value


def _packet(value, task_id):
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    files.decode_packet(raw, expected_task_id=task_id)
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
        files.fail("review_target_mismatch")
    files.validate_packet_target(source["review_target"])
    if action == "target":
        route = source["verification_route"]
        if (route not in ("not_required", "runner_pass", "receipt_required", "blocked")
                or (source["blocking_code"] not in {"verification_receipt_blocking", "verification_requirement_unspecified"}
                    if route == "blocked" else source["blocking_code"] is not None)
                or (route in ("not_required", "runner_pass")) != (preparation["status"] in ("ready", "failed"))):
            files.fail("handoff_response_invalid")
    elif ((source["result"] == "pass" and source["scope_coverage"] == "full")
          != (preparation["status"] in ("ready", "failed"))):
        files.fail("handoff_response_invalid")
    return source, preparation


def add_material_parser(commands):
    parser = commands.add_parser("material", help="Read immutable Git review material; no arbitrary Git arguments")
    parser.add_argument("--repo", required=True)
    actions = parser.add_subparsers(dest="material_operation", required=True)
    actions.add_parser("blob").add_argument("object_id")
    actions.add_parser("batch", help="Full blob IDs on stdin, one per line; bounded JSON delivery")
    diff = actions.add_parser("diff")
    diff.add_argument("before")
    diff.add_argument("after")
    for name in ("dependency", "directory"):
        action = actions.add_parser(name)
        action.add_argument("revision")
        action.add_argument("--path", required=True, help="Project-relative path; directory accepts empty root")
    collect = actions.add_parser("collect", help="Bound target bodies/diffs plus selected dependency paths as UTF-8 JSON stdin")
    collect.add_argument("--packet", required=True)
    collect.add_argument("--packet-sha256", required=True)
    collect.add_argument("--selection-sha256")
    for name in ("changes", "inventory"):
        action = actions.add_parser(name, help="Explicit whole-entry list at the fixed Packet target")
        action.add_argument("--packet", required=True)
        action.add_argument("--packet-sha256", required=True)
    for action in actions.choices.values():
        action.add_argument("--start", type=int, default=0, help="Returned next_start: UTF-8 byte offset for text, entry offset for lists/collection")
        if action.prog.split()[-1] not in ("collect", "changes", "inventory"):
            action.add_argument("--content-sha256", help="Returned content digest when continuing a range")


def read_material(repo, args):
    """Serialize a bounded complete response only after immutable reads succeed."""
    if args.material_operation == "collect":
        from task_governance_tool.review_material_collection import collect_material
        result = collect_material(repo, args.packet, args.packet_sha256, sys.stdin.buffer,
                                  start=args.start, selection_sha256=args.selection_sha256,
                                  records_only=args.records_only)
    else:
        from task_governance_tool.review_material_reads import read
        from task_governance_tool.artifact_manifest import ArtifactManifestError
        from task_governance_tool.git_snapshot import GitSnapshotError
        try:
            result = read(repo, args)
        except (ArtifactManifestError, GitSnapshotError) as exc:
            files.fail(exc.code)
    return (1 if result["status"] == "incomplete" else 0) if files.emit_json(result, utf8=True) else 1


def _material_command(repo, operation, *arguments, records_only=False):
    entry = Path(__file__).parent.parent / "review_handoff.py"
    return _shell([sys.executable, "-I", "-S", "-B", str(entry), *(["--records-only"] if records_only else []), "material", "--repo=" + str(repo),
                   operation, *arguments])


def _dependency_inventory(repo, revision, changes, *, start=0):
    """Bounded location hints, not inferred relevance or observed blob content."""
    from task_governance_tool.artifact_manifest import decode_artifact_path
    from task_governance_tool.git_snapshot import stream_tree_entries

    changed = {entry[key] for entry in changes for key in ("old_path", "new_path")
               if entry[key] is not None}
    entries = []
    total = 0
    size = 2
    truncated = False

    def collect(entry):
        nonlocal total, size, truncated
        path = decode_artifact_path(entry.path)
        if path in changed:
            return
        total += 1
        if total <= start:
            return
        row = {"path": path, "mode": entry.mode.decode("ascii"),
               "object_id": entry.object_id.decode("ascii")}
        addition = len(json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) + bool(entries)
        if truncated or len(entries) == INVENTORY_ENTRY_LIMIT or size + addition > INVENTORY_BYTE_LIMIT:
            truncated = True
            return
        entries.append(row)
        size += addition

    stream_tree_entries(repo, revision, object_id_length=len(revision), consume_entry=collect)
    return {"entries": entries, "total": total,
            "returned": len(entries), "truncated": truncated}


def _observe_material(repo, target):
    """Shared exact-target observation; no mutable path is used as source content."""
    from task_governance_tool.artifact_manifest import (
        ArtifactManifestError,
        observe_git_commit_manifest, observe_staged_git_manifest,
    )
    from task_governance_tool.completion import CompletionEvidenceError
    from task_governance_tool.git_snapshot import GitSnapshotError
    if target["kind"] not in ("git_snapshot", "git_commit"):
        files.fail("review_material_requires_supplied")
    try:
        observed = (observe_staged_git_manifest(repo) if target["kind"] == "git_snapshot"
                    else observe_git_commit_manifest(repo, target["value"]))
        if (observed.target_value != target["value"]
                or observed.target_base_revision != target["base_revision"]):
            files.fail("review_target_mismatch")
        return observed
    except (ArtifactManifestError, GitSnapshotError, CompletionEvidenceError) as exc:
        files.fail(exc.code)


def _review_material(repo, target, *, material_details=False, records_only=False):
    """Expose selectors and location hints, never source bodies in the first read."""
    from task_governance_tool.artifact_manifest import (
        ArtifactManifestError, ARTIFACT_MANIFEST_BYTE_LIMIT, build_artifact_entries,
    )
    from task_governance_tool.git_snapshot import GitSnapshotError
    if target["kind"] not in ("git_snapshot", "git_commit"):
        return {"status": "requires_supplied_material", "changes": None,
                "instructions": [
                    "The saved target cannot retrieve diff/external content. Obtain the complete supplied material and evidence binding it to review_target.value from the caller before PASS; do not substitute Git HEAD or worktree files."]}
    observed = _observe_material(repo, target)
    changes = [asdict(entry) for entry in build_artifact_entries(observed.before_leaves, observed.after_leaves)]
    dependency_revision = target["base_revision"] if target["kind"] == "git_snapshot" else target["value"]
    # The existing manifest limit remains a complete-result boundary, not a
    # shortened list that a reviewer might mistake for the whole target.
    if len(json.dumps(changes, ensure_ascii=False).encode("utf-8")) > ARTIFACT_MANIFEST_BYTE_LIMIT:
        files.fail("artifact_manifest_too_large")
    # The placeholder is inside a shell literal; state the host's exact
    # substitution rule rather than asking the reviewer to repair quoting.
    path_quoting = ("When substituting <project-relative-path>, first double each PowerShell single-quote delimiter in the path: U+0027 ('), U+2018 (‘), U+2019 (’), U+201A (‚), U+201B (‛). Use two copies of that same character, never replace a smart quote with ASCII. Keep all other characters unchanged and keep the surrounding command quotes."
                    if os.name == "nt" else
                    "When substituting <project-relative-path>, first replace each single quote (') in the path with the five-character sequence '\"'\"'; keep all other characters unchanged and keep the surrounding command quotes.")
    return {"status": "git_objects_verified", "changes": changes,
            "comparison_base": observed.comparison_base,
            "dependency_revision": dependency_revision,
            "blob_command": _material_command(repo, "blob", "<object_id>", records_only=records_only),
            "blob_batch_command": _material_command(repo, "batch", records_only=records_only),
            "diff_command": _material_command(repo, "diff", "<before_object_id>", "<after_object_id>", records_only=records_only),
            "dependency_command": _material_command(repo, "dependency", dependency_revision, "--path=<project-relative-path>", records_only=records_only),
            "directory_command": _material_command(repo, "directory", dependency_revision, "--path=<project-relative-directory>", records_only=records_only),
            "instructions": ["For individual dependency/directory templates: " + path_quoting]}


def read_for_reviewer(repo, packet_path, *, material_details=False, records_only=False,
                      section="all", start=0, content_sha256=None, packet_sha256=None):
    """Validate saved data, bind exact material, then compare live public context."""
    from task_governance_tool.review_packet import independent_reviewer_view
    path = files.checked_path(repo, packet_path)
    raw = files.read_bounded(path, files.PACKET_LIMIT)
    from hashlib import sha256
    from task_governance_tool.review_delivery import GUIDANCE as DELIVERY_GUIDANCE
    if packet_sha256 is not None and sha256(raw).hexdigest() != packet_sha256:
        files.fail("review_packet_stale")
    material_details = material_details or section in ("material", "workspace")
    packet = files.decode_packet(raw)
    material = _review_material(repo, packet["review_target"], material_details=material_details, records_only=records_only)
    if material["status"] == "git_objects_verified":
        from hashlib import sha256
        command = _material_command(
            repo, "collect", "--packet=" + packet_path, "--packet-sha256=" + sha256(raw).hexdigest(), records_only=records_only)
        placeholder = "<selected dependency paths as JSON array>"
        material["collect_command"] = (
            "$OutputEncoding = [Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)\n@'\n"
            + placeholder + "\n'@ | " + command if os.name == "nt" else
            command + " <<'TASKGOV_MATERIAL_PATHS'\n" + placeholder + "\nTASKGOV_MATERIAL_PATHS")
        reuse_guidance = "Reuse a body already received completely only when its immutable object identity or exact revision/path matches this target's mapping. An ambient filename, summary, prior review or incomplete/unknown delivery is not proof. Omit already-held matching dependencies from collection; use the same text for authority reading and review. A changed file's before/after sides and diff remain necessary. Project reread obligations still apply."
        if not material_details:
            # Keep exact target data and normal collection together.
            # The optional read retains the same full validation; it is not a
            # prerequisite or a second format lookup before an ordinary save.
            for key in ("blob_command", "blob_batch_command", "diff_command",
                        "dependency_command", "directory_command"):
                del material[key]
            entry = Path(__file__).parent.parent / "review_handoff.py"
            material["recovery_command"] = _shell([
                sys.executable, *(["-I", "-S"] if records_only else []), "-B", str(entry), *(["--records-only"] if records_only else []), "read", "--repo=" + str(repo),
                "--packet=" + packet_path, "--material-details"])
        material["changes_command"] = _material_command(repo, "changes", "--packet=" + packet_path,
            "--packet-sha256=" + sha256(raw).hexdigest(), records_only=records_only)
        material["inventory_command"] = _material_command(repo, "inventory", "--packet=" + packet_path,
            "--packet-sha256=" + sha256(raw).hexdigest(), records_only=records_only)
        quoting = material["instructions"][-1] if material_details else None
        material["instructions"] = [
            "collect_command accepts the JSON array of needed dependency paths ([] for none) and includes changed before/after bodies and diffs. Read governing text before judgment and follow its routes. Known paths need no inventory. Use fixed IDs/revisions, never ambient HEAD/index/worktree content. Deleted/renamed old paths are absent; unchanged snapshot dependencies use the base commit. Mode 120000 is link text, 160000 needs supplied material.",
            reuse_guidance,
            "changes_command pages every changed entry if the first read or collection is split. Only explicit inventory_command lists unchanged files: at most 20 entries and 4096 compact UTF-8 JSON-array bytes, with remaining/next. Individual recovery instructions never imply inventory retrieval. Use listed commands for missing ranges; batch supplies bounded bodies with per-ID status, directory supplies exact fixed-revision text ranges. Recovery does not repeat successful siblings.",
            DELIVERY_GUIDANCE,
        ]
        if quoting:
            material["instructions"].append(quoting)
    current = validate_live_packet(repo, path, raw, packet, records_only=records_only)
    from task_governance_tool.review_verification import GUIDANCE
    if "verification_evidence" not in current["data"]:
        files.fail("handoff_response_invalid")
    if material["status"] == "git_objects_verified" and not records_only:
        from task_governance_tool.review_workspace import workspace_guidance
        material["verification_workspace"] = workspace_guidance(repo, packet_path, raw, details=material_details)
    view = {**independent_reviewer_view(packet), "review_material": material,
            "verification_evidence": current["data"]["verification_evidence"],
            "verification_guidance": GUIDANCE,
            "context_check": "matched_at_read", "warnings": current["warnings"]}
    from task_governance_tool.review_read_delivery import bound
    return bound(view, repo, packet_path, raw, section=section, start=start, expected=content_sha256, records_only=records_only)


def validate_live_packet(repo, path, raw, packet, *, records_only=False):
    """Compare one complete saved Packet with the existing public read."""
    # One existing read-only public operation, inside the replacement read;
    # no extra reviewer check/show, new target, Receipt or direct DB access.
    entry = Path(__file__).parent.parent / "taskgov.py"
    command = [sys.executable, "-B", str(entry), "review", "prepare",
               packet["task"]["task_id"], "--repo", str(repo), "--json"]
    if records_only:
        command = [sys.executable, "-I", "-S", "-B", str(entry), "--records-only", *command[3:]]
    code, response = _capture(command, None, lambda: None)
    current = _envelope(response, code, "review.prepare")
    if not current["ok"]:
        files.fail(current["errors"][0]["code"])
    _packet(current["data"], packet["task"]["task_id"])
    for key in ("task", "contract", "review_target", "changed_paths_available", "changed_paths",
                "changed_paths_total", "changed_paths_truncated"):
        compared = current["data"][key]
        if key == "contract" and "authority_ref" not in packet[key]:
            # Compare the old saved shape without inventing a reference or
            # changing its bytes/display. Every pre-existing field still matches.
            compared = {name: value for name, value in compared.items() if name != "authority_ref"}
        if compared != packet[key]:
            files.fail("review_packet_stale")
    if current["data"].get("review_session_context") != packet.get("review_session_context"):
        files.fail("review_packet_stale")
    if files.read_bounded(path, files.PACKET_LIMIT) != raw:
        files.fail("handoff_file_changed")
    return current


def _shell(arguments):
    if os.name == "nt":
        escapes = {ord(quote): quote * 2 for quote in "'‘’‚‛"}
        return "& " + " ".join("'" + value.translate(escapes) + "'" for value in arguments)
    return shlex.join(arguments)


def _requests(repo, args, packet_path):
    entry = str(Path(__file__).parent.parent / "review_handoff.py")
    base = [sys.executable, "-B", entry]
    if getattr(args, "records_only", False) is True:
        base = [sys.executable, "-I", "-S", "-B", entry, "--records-only"]
    common = ["--repo=" + str(repo), "--packet=" + packet_path]
    read = _shell([*base, "read", *common])
    reviewers = []
    paths = [args.directory + f"/review-{index}.json" for index in range(1, args.reviewers + 1)]
    for path in paths:
        save = _shell([*base, "save", *common, "--output=" + path])
        if os.name == "nt":
            invocation = "$OutputEncoding = [Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)\n@'\n<completed original JSON>\n'@ | " + save
        else:
            invocation = save + " <<'TASKGOV_REVIEW_RESULT'\n<completed original JSON>\nTASKGOV_REVIEW_RESULT"
        reviewers.append({"result_path": path, "request": (
            "You are assigned an independent review of the complete exact target under current project authority. "
            "Reserve at least 24000 output tokens in both execution and outer return; one response per call without batching or echoing. "
            "Run the fixed read below and follow its result_instructions with the supplied material and verification guidance. "
            "Follow explicit section/page/range commands until required context and material are complete. Recover only missing fixed-target content; if identity or complete recovery cannot be established, report the insufficiency to the caller before judgment."
            + "\n\nRead command:\n" + read + "\n\nSave command:\n" + invocation
        )})
    from task_governance_tool.setup_feature_config import read_choices
    try:
        choices = read_choices(Path(__file__).absolute().parents[2])[1]
        wait_status = "enabled" if choices.get("review_wait") is True else "disabled"
    except Exception:
        wait_status = "unavailable"
    return {"status": "ready", "packet_path": packet_path, "review_requests": reviewers,
            "review_wait": {"status": wait_status, "task_id": args.task_id,
                            "wait_tool": "review_wait_wait",
                            "guide": "references/review_wait.md#normal-wait"},
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
            files.require_ignored(repo, path)
        code, response = _capture(command, raw, lambda: result.update(operation_status="unknown"))
        result["source_exit_code"] = code
        envelope = _envelope(response, code, result["command"])
        result.update(operation_status="succeeded" if envelope["ok"] else "failed",
                      warnings=envelope["warnings"], errors=envelope["errors"])
        if not envelope["ok"]:
            # Target-set errors do not report whether T1 or restart cleanup
            # committed. A parser rejection precedes dispatch; other target
            # failures cannot establish no-write from their code or exit value.
            if (args.source_operation == "target" and envelope["command"] != "parse"
                    and not (getattr(args, "records_only", False) is True
                             and [error["code"] for error in envelope["errors"]] == ["runner_execution_requires_authorization"])):
                result["operation_status"] = "unknown"
                result["warnings"] = [*result["warnings"], {
                    "code": "handoff_outcome_unknown",
                    "message": "Source command failed; saved state is unconfirmed. Inspect public state before retry.",
                }]
            return result
        source, preparation = _source(envelope["data"], args)
        result["source"] = source
        status = preparation["status"]
        if status not in ("ready", "failed", "blocked", "not_applicable"):
            files.fail("handoff_response_invalid")
        errors = _messages(preparation["errors"])
        if status != "ready":
            if preparation["packet"] is not None:
                files.fail("handoff_response_invalid")
            result.update(ok=status == "not_applicable", errors=errors, handoff={"status": status})
            return result
        if errors:
            files.fail("handoff_response_invalid")
        packet = preparation["packet"]
        packet_bytes = _packet(packet, args.task_id)
        if source and (source["review_target"] != packet["review_target"]
                       or source.get("contract_revision", packet["contract"]["revision"]) != packet["contract"]["revision"]):
            files.fail("review_target_mismatch")
        # Recheck before the first write; no path created by another caller is adopted.
        files.check_parents(observed)
        for path in missing:
            parents = files.observe_parents(path)
            files.require_ignored(repo, path.relative_to(repo).as_posix() + "/")
            # Windows treats 0700 as a protected DACL, blocking parent grants.
            if os.name == "nt":
                path.mkdir()
            else:
                path.mkdir(mode=0o700)
            files.require_physical(path.lstat(), directory=True)
            files.check_parents(parents)
        parents = files.observe_parents(destination / "packet.json")
        target = files.checked_path(repo, packet_path)
        files.write_exclusive(target, packet_bytes)
        saved = files.read_bounded(target, files.PACKET_LIMIT)
        if saved != packet_bytes or _packet(_json(saved), args.task_id) != packet_bytes:
            files.fail("handoff_file_changed")
        for path in result_paths:
            target_result = files.checked_path(repo, path)
            if os.path.lexists(target_result):
                files.fail("handoff_destination_exists")
        files.check_parents(parents)
        result.update(ok=True, handoff=_requests(repo, args, packet_path))
        if getattr(args, "operation", "prepare") == "prepare-finalization":
            from task_governance_tool.review_finalization import prepare_intent
            try:
                intent = prepare_intent(repo, packet_path, result_paths, session_identity.capture_caller_identity())
            except Exception as exc:
                from task_governance_tool.review_handoff_scope import error_code as _code
                files.fail(_code(exc))
            entry = str(Path(__file__).parent.parent / "review_handoff.py")
            result["handoff"]["finalization"] = intent
            result["handoff"]["finalization_command"] = _shell([
                sys.executable, "-B", entry, "finalize", "--repo=" + str(repo), "--task-id=" + args.task_id,
                "--target-generation=" + str(intent["target_generation"])])
            # The finalizer owns registration for this intent. Do not present
            # an independent parent-side submission as another normal next step.
            del result["handoff"]["submit_command"]
        from task_governance_tool.review_preparation import publish_ready
        try:
            locator = publish_ready(repo, packet_path, result_paths, session_identity.capture_caller_identity(),
                route="integrated" if getattr(args, "operation", "prepare") == "prepare-finalization" else "manual")
        except Exception as exc:
            from task_governance_tool.review_handoff_scope import error_code as _code
            files.fail(_code(exc))
        if locator is not None:
            result["handoff"]["preparation"] = locator
        return result
    except (files.HandoffError, ReviewEvidenceError, TaskValidationError, VerificationReceiptError) as exc:
        code = exc.code
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RecursionError, subprocess.SubprocessError):
        code = "handoff_io_or_input_failed"
    except KeyboardInterrupt:
        code = "handoff_outcome_unknown"
    result.update(ok=False, handoff={"status": "failed"}, errors=[{
        "code": code, "message": "Handoff preparation failed; preserve state and residue, inspect the source outcome before retry."
    }])
    return result
