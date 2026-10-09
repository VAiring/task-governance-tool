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

from task_governance_tool import review_handoff as files
from task_governance_tool.task_values import validate_task_id
from task_governance_tool.verification_results import (
    VERIFICATION_RESULT_INPUT_LIMIT, decode_verification_result,
)
from task_governance_tool.verification_receipts import VerificationReceiptError

RESPONSE_LIMIT = 262144
INVENTORY_ENTRY_LIMIT = 128
INVENTORY_BYTE_LIMIT = 16384
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
            files._fail("handoff_invalid_arguments")
        raw = sys.stdin.buffer.read(VERIFICATION_RESULT_INPUT_LIMIT + 1)
        decode_verification_result(raw, task_id=task_id)
        arguments.append("--from-stdin")
    entry = Path(__file__).parent.parent / "taskgov.py"
    if getattr(args, "records_only", False) is True:
        return [sys.executable, "-I", "-S", "-B", str(entry), "--records-only", *arguments, "--repo", str(repo), "--json"], raw
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
    if not isinstance(raw, bytes):
        files._fail("handoff_response_invalid")
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


def _packet(value, task_id):
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    files._packet(raw, expected_task_id=task_id)
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
    files._packet_target(source["review_target"])
    if action == "target":
        route = source["verification_route"]
        if (route not in ("not_required", "runner_pass", "receipt_required", "blocked")
                or (source["blocking_code"] not in {"verification_receipt_blocking", "verification_requirement_unspecified"}
                    if route == "blocked" else source["blocking_code"] is not None)
                or (route in ("not_required", "runner_pass")) != (preparation["status"] in ("ready", "failed"))):
            files._fail("handoff_response_invalid")
    elif ((source["result"] == "pass" and source["scope_coverage"] == "full")
          != (preparation["status"] in ("ready", "failed"))):
        files._fail("handoff_response_invalid")
    return source, preparation


def add_material_parser(commands):
    parser = commands.add_parser("material", help="Read immutable Git review material; no arbitrary Git arguments")
    parser.add_argument("--repo", required=True)
    actions = parser.add_subparsers(dest="material_operation", required=True)
    actions.add_parser("blob").add_argument("object_id")
    actions.add_parser("batch", help="Full blob IDs on stdin, one per line; raw Git batch framing")
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


def read_material(repo, args):
    """Closed immutable reads, streaming raw Git bytes without a success envelope."""
    from task_governance_tool.artifact_manifest import ArtifactManifestError, validate_artifact_path
    from task_governance_tool.completion import FULL_GIT_OBJECT_ID, safe_git_command, safe_git_environment

    def object_id(value):
        if not FULL_GIT_OBJECT_ID.fullmatch(value):
            files._fail("handoff_invalid_arguments")
        return value

    action = args.material_operation
    if action == "collect":
        from task_governance_tool.review_material_collection import collect_material
        result = collect_material(repo, args.packet, args.packet_sha256, sys.stdin.buffer)
        return (0 if result["status"] == "complete" else 1) if files._emit(result, utf8=True) else 1
    if action == "blob":
        arguments = ["cat-file", "blob", object_id(args.object_id)]
    elif action == "diff":
        arguments = ["diff", "--no-ext-diff", "--no-textconv", object_id(args.before), object_id(args.after), "--"]
    elif action in ("dependency", "directory"):
        revision = object_id(args.revision)
        try:
            if args.path or action != "directory":
                validate_artifact_path(args.path)
        except ArtifactManifestError as exc:
            files._fail(exc.code)
        spec = revision + ":" + args.path
        arguments = (["show", spec] if action == "dependency" else
                     ["-c", "core.quotePath=false", "ls-tree", "--full-tree", "--no-abbrev", spec, "--"])
    elif action == "batch":
        # Validate each short ID before forwarding it, without retaining bodies
        # or imposing a new batch-count cap. Output keeps Git's missing/type framing.
        with subprocess.Popen([*safe_git_command(repo), "cat-file", "--batch"], stdin=subprocess.PIPE,
                              shell=False, env=safe_git_environment()) as process:
            try:
                while line := sys.stdin.buffer.readline(67):
                    value = line.removesuffix(b"\n").removesuffix(b"\r").decode("ascii")
                    process.stdin.write(object_id(value).encode("ascii") + b"\n")
                    process.stdin.flush()
                process.stdin.close()
                return process.wait()
            except BaseException:
                process.terminate()
                raise
    else:
        files._fail("handoff_invalid_arguments")
    return subprocess.run([*safe_git_command(repo), *arguments], stdin=subprocess.DEVNULL,
                          shell=False, env=safe_git_environment(), check=False).returncode


def _material_command(repo, operation, *arguments, records_only=False):
    entry = Path(__file__).parent.parent / "review_handoff.py"
    return _shell([sys.executable, "-I", "-S", "-B", str(entry), *(["--records-only"] if records_only else []), "material", "--repo=" + str(repo),
                   operation, *arguments])


def _dependency_inventory(repo, revision, changes):
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
        files._fail("review_material_requires_supplied")
    try:
        observed = (observe_staged_git_manifest(repo) if target["kind"] == "git_snapshot"
                    else observe_git_commit_manifest(repo, target["value"]))
        if (observed.target_value != target["value"]
                or observed.target_base_revision != target["base_revision"]):
            files._fail("review_target_mismatch")
        return observed
    except (ArtifactManifestError, GitSnapshotError, CompletionEvidenceError) as exc:
        files._fail(exc.code)


def _review_material(repo, target, *, records_only=False):
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
    try:
        inventory = _dependency_inventory(repo, dependency_revision, changes)
    except (ArtifactManifestError, GitSnapshotError) as exc:
        files._fail(exc.code)
    # The existing manifest limit remains a complete-result boundary, not a
    # shortened list that a reviewer might mistake for the whole target.
    if len(json.dumps(changes, ensure_ascii=False).encode("utf-8")) > ARTIFACT_MANIFEST_BYTE_LIMIT:
        files._fail("artifact_manifest_too_large")
    # The placeholder is inside a shell literal; state the host's exact
    # substitution rule rather than asking the reviewer to repair quoting.
    path_quoting = ("When substituting <project-relative-path>, first double each PowerShell single-quote delimiter in the path: U+0027 ('), U+2018 (‘), U+2019 (’), U+201A (‚), U+201B (‛). Use two copies of that same character, never replace a smart quote with ASCII. Keep all other characters unchanged and keep the surrounding command quotes."
                    if os.name == "nt" else
                    "When substituting <project-relative-path>, first replace each single quote (') in the path with the five-character sequence '\"'\"'; keep all other characters unchanged and keep the surrounding command quotes.")
    return {"status": "git_objects_verified", "changes": changes,
            "comparison_base": observed.comparison_base,
            "dependency_revision": dependency_revision,
            "unchanged_inventory": inventory,
            "blob_command": _material_command(repo, "blob", "<object_id>", records_only=records_only),
            "blob_batch_command": _material_command(repo, "batch", records_only=records_only),
            "diff_command": _material_command(repo, "diff", "<before_object_id>", "<after_object_id>", records_only=records_only),
            "dependency_command": _material_command(repo, "dependency", dependency_revision, "--path=<project-relative-path>", records_only=records_only),
            "directory_command": _material_command(repo, "directory", dependency_revision, "--path=<project-relative-directory>", records_only=records_only),
            "instructions": [
                "Run collect_command, replacing only its JSON-array placeholder with the dependency paths you have selected (for example [\"AGENTS.md\",\"docs/authority.md\"], or []). It retrieves every changed before/after body and diff automatically, plus those dependencies; no OID list or parent selection is needed. Its bodies table supplies each blob once and preserves every path/revision/side association. Reuse provided complete bodies; do not reread merely because they were supplied. Status incomplete/nonzero, unavailable/large/non-text records or tool truncation require affected-material recovery, not repetition of successful siblings or PASS.",
                "changes is the complete delta even when Packet paths are bounded. Inspect every entry and mode. Mode 120000 is link text, never followed; 160000 is unavailable submodule material to obtain from the caller. Whole-file add/delete diffs refer to the complete supplied body; two-sided diffs are Git patches. Delivery is not review coverage, quality or provenance.",
                "unchanged_inventory is bounded location guidance, not selected relevance or proof of availability. Use actual authority/import references to select dependencies; follow newly discovered ones afterward. Snapshot changed paths use after objects, removed/renamed old paths are absent, unchanged paths use the base commit; commit targets use their exact commit. Never substitute ambient HEAD/index/worktree content.",
                "If locations are omitted or more discovery is needed, directory_command lists that exact revision's directory (empty placeholder for root; otherwise path without trailing slash, escaped by the same path rule). Its paths are relative to that directory; mode 040000 entries can be explored with the same command. Apply changes over this base listing: changed snapshot files use after objects, removed/renamed old paths are absent. Recover tool-truncated listings by requesting sufficient output or exploring narrower directories; report unresolved omissions, never use ambient file search as target material.",
                "Recovery/additional reads use blob_command, diff_command (both sides present), dependency_command (unchanged paths at dependency_revision), or blob_batch_command (listed full IDs, one per stdin line). Batch returns '<id> blob <byte-count>\\n', exactly that many bytes, then LF, per input. Check each record: exit zero may still report missing/non-blob content. The fixed commands disable fetch, replacement refs, locks and prompts; no body enters the Packet or Task DB. Nonzero or incomplete delivery is not success.",
                "For the individual dependency/directory recovery templates only: " + path_quoting]}


def read_for_reviewer(repo, packet_path, *, material_details=False, records_only=False):
    """Validate saved data, bind exact material, then compare live public context."""
    from task_governance_tool.review_packet import independent_reviewer_view
    path = files._path(repo, packet_path)
    raw = files._read(path, files.PACKET_LIMIT)
    packet = files._packet(raw)
    material = _review_material(repo, packet["review_target"], records_only=records_only)
    if material["status"] == "git_objects_verified":
        from hashlib import sha256
        command = _material_command(
            repo, "collect", "--packet=" + packet_path, "--packet-sha256=" + sha256(raw).hexdigest(), records_only=records_only)
        placeholder = "<selected dependency paths as JSON array>"
        material["collect_command"] = (
            "$OutputEncoding = [Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)\n@'\n"
            + placeholder + "\n'@ | " + command if os.name == "nt" else
            command + " <<'TASKGOV_MATERIAL_PATHS'\n" + placeholder + "\nTASKGOV_MATERIAL_PATHS")
        reuse_guidance = "Reuse a body already received completely only when its immutable object identity or exact revision/path matches this target's mapping. An ambient filename, summary, prior review or incomplete/unknown delivery is not proof. Omit already-held matching dependencies from collection; use returned bodies for both authority reading and review, without repeating them for a different purpose. A changed file's before/after sides and diff remain necessary. Project reread obligations still apply. Each reviewer independently reads and judges the complete scope; no hash calculation or reading ledger is needed."
        if material_details:
            material["instructions"].insert(1, reuse_guidance)
        else:
            # Keep exact target/location data and normal collection together.
            # The optional read retains the same full validation; it is not a
            # prerequisite or a second format lookup before an ordinary save.
            for key in ("blob_command", "blob_batch_command", "diff_command",
                        "dependency_command", "directory_command"):
                del material[key]
            entry = Path(__file__).parent.parent / "review_handoff.py"
            material["recovery_command"] = _shell([
                sys.executable, *(["-I", "-S"] if records_only else []), "-B", str(entry), *(["--records-only"] if records_only else []), "read", "--repo=" + str(repo),
                "--packet=" + packet_path, "--material-details"])
            material["instructions"] = [
                "Use collect_command for the first required project AGENTS, authority, source and test reads, selecting the dependency paths you need as its JSON array ([] for none). All changed before/after bodies and diffs are automatic. Read the returned governing text before judging code, then follow its routes and discovered dependencies. The parent does not select material.",
                reuse_guidance,
                "changes is complete even if Packet paths are bounded; unchanged_inventory is only bounded location guidance, not selected relevance or availability. Snapshot changed paths use after objects, deleted/renamed old paths are absent, unchanged paths use the base commit; commit targets use their exact commit. Never substitute ambient HEAD/index/worktree content. Modes, path/side mappings and whole-file diff body references remain part of the material.",
                "Collection complete/exit zero means delivery, not review coverage or PASS. Missing, mismatched, unknown, tool-truncated, large/non-text or unavailable material still needs retrieval; submodule material must be supplied. Use recovery_command only when individual/additional reads or discovery are needed; it supplies their commands and quoting rules. Recover affected material without repeating successful siblings. Keep unknown provenance unknown and use the complete result_instructions below for normal save.",
            ]
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
        files._fail(current["errors"][0]["code"])
    _packet(current["data"], packet["task"]["task_id"])
    for key in ("task", "contract", "review_target", "changed_paths_available", "changed_paths",
                "changed_paths_total", "changed_paths_truncated"):
        compared = current["data"][key]
        if key == "contract" and "authority_ref" not in packet[key]:
            # Compare the old saved shape without inventing a reference or
            # changing its bytes/display. Every pre-existing field still matches.
            compared = {name: value for name, value in compared.items() if name != "authority_ref"}
        if compared != packet[key]:
            files._fail("review_packet_stale")
    if current["data"].get("review_session_context") != packet.get("review_session_context"):
        files._fail("review_packet_stale")
    if files._read(path, files.PACKET_LIMIT) != raw:
        files._fail("handoff_file_changed")
    return {**independent_reviewer_view(packet), "review_material": material,
            "context_check": "matched_at_read", "warnings": current["warnings"]}


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
            "This request and its read output supply your procedure and result format; no Skill operating guide or internal fingerprint implementation is a prerequisite. "
            "Obtain the Task/Contract, criteria, exact material access and result template with this read-only operation:\n"
            + read + "\nUse review_material to obtain or reuse complete target-bound project AGENTS.md and applicable authority before judging the whole target against its scope, acceptance, constraints and verification expectation. "
            "Follow review_material for exact artifacts, required source/tests, discovered dependencies and retrieval exceptions. "
            "Read Skill files when they are actually governing or reviewed material, not to learn Task management. "
            "If this role does not match actual work, ask the caller, "
            "do not infer independence. "
            "Complete the view's result_template using result_instructions, actual judgment and provenance, and all Findings with severity, exact file/line, risks and recommended correction in bounded summaries. "
            "Do not search internals to guess unknown model/Skill identity or version. "
            "Replace only the JSON placeholder below; run this fixed save operation, not new save/validation code.\n"
            + invocation + "\nOn saved acknowledgement return path, verdict and Finding count once in the final response; "
            "do not echo JSON or send a duplicate normal-success notification. "
            "Report questions, read mismatch, unavailable/truncated material, unknown role, save failure or lost acknowledgement to the caller; do not claim PASS from incomplete inspection or success from an unknown outcome. "
            "Preserve failed residue; do not overwrite or blindly repeat a save. Do not manage Tasks, reset targets, register DB evidence, complete work or implement transport/recovery code."
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
            files._ignored(repo, path)
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
            # Windows treats 0700 as a protected DACL, blocking parent grants.
            if os.name == "nt":
                path.mkdir()
            else:
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
        if getattr(args, "operation", "prepare") == "prepare-finalization":
            from task_governance_tool.review_finalization import prepare_intent
            try:
                intent = prepare_intent(repo, packet_path, result_paths, files.capture_caller_identity())
            except Exception as exc:
                from task_governance_tool.review_finalization import _code
                files._fail(_code(exc))
            entry = str(Path(__file__).parent.parent / "review_handoff.py")
            result["handoff"]["finalization"] = intent
            result["handoff"]["finalization_command"] = _shell([
                sys.executable, "-B", entry, "finalize", "--repo=" + str(repo), "--task-id=" + args.task_id,
                "--target-generation=" + str(intent["target_generation"])])
            # The finalizer owns registration for this intent. Do not present
            # an independent parent-side submission as another normal next step.
            del result["handoff"]["submit_command"]
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
