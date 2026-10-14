"""Fixed-object range and optional inventory reads; no persistent state."""

from __future__ import annotations

import hashlib
import sys

from task_governance_tool import review_handoff as files
from task_governance_tool.artifact_manifest import ARTIFACT_MANIFEST_BYTE_LIMIT, validate_artifact_path
from task_governance_tool.completion import FULL_GIT_OBJECT_ID
from task_governance_tool.review_delivery import GUIDANCE, encode, fits, git_range, require_fit


def read(repo, args):
    from task_governance_tool.review_handoff_preparation import _material_command, _observe_material, _dependency_inventory
    from task_governance_tool.artifact_manifest import build_artifact_entries
    from dataclasses import asdict

    action = args.material_operation
    start = args.start
    if start < 0:
        files._fail("handoff_invalid_arguments")
    command = lambda operation, *values: _material_command(repo, operation, *values, records_only=args.records_only)

    def oid(value):
        if not FULL_GIT_OBJECT_ID.fullmatch(value):
            files._fail("handoff_invalid_arguments")
        return value

    if action in ("inventory", "changes"):
        path = files._path(repo, args.packet)
        raw = files._read(path, files.PACKET_LIMIT)
        if hashlib.sha256(raw).hexdigest() != args.packet_sha256:
            files._fail("review_packet_stale")
        packet = files._packet(raw)
        target = packet["review_target"]
        observed = _observe_material(repo, target)
        changes = [asdict(entry) for entry in build_artifact_entries(observed.before_leaves, observed.after_leaves)]
        revision = target["base_revision"] if target["kind"] == "git_snapshot" else target["value"]
        if action == "inventory":
            page = _dependency_inventory(repo, revision, changes, start=start)
        else:
            page = {"entries": changes[start:start + 20], "total": len(changes)}
        if start > page["total"]:
            files._fail("handoff_invalid_arguments")

        def output():
            end = start + len(page["entries"])
            return {"status": "list", "review_target": target, "packet_sha256": args.packet_sha256,
                    "dependency_revision": revision, "entries": page["entries"], "start": start,
                    "end": end, "total": page["total"], "remaining": page["total"] - end,
                    "next_start": end if end < page["total"] else None,
                    "next_command": command(action, "--packet=" + args.packet, "--packet-sha256=" + args.packet_sha256,
                                            "--start=" + str(end)) if end < page["total"] else None,
                    "instructions": "Whole entries, stable Git order; omitted entries remain in scope. " + GUIDANCE}
        result = output()
        while not fits(result) and page["entries"]:
            page["entries"].pop()
            result = output()
        if not page["entries"] and start < page["total"]:
            files._fail("review_delivery_budget_exceeded")
        if files._read(path, files.PACKET_LIMIT) != raw:
            files._fail("handoff_file_changed")
        return require_fit(result)
    if action == "batch":
        raw = sys.stdin.buffer.read(ARTIFACT_MANIFEST_BYTE_LIMIT + 1)
        if len(raw) > ARTIFACT_MANIFEST_BYTE_LIMIT:
            files._fail("handoff_invalid_arguments")
        ids = [oid(line.decode("ascii")) for line in raw.splitlines()]
        digest = hashlib.sha256(encode(ids)).hexdigest()
        if start > len(ids) or (args.content_sha256 is not None and args.content_sha256 != digest):
            files._fail("review_material_range_mismatch")
        end = min(start + 20, len(ids))
        from task_governance_tool.review_material_collection import _Bodies
        bodies = _Bodies(repo)
        for value in ids[start:end]:
            bodies.blob(value)

        def output():
            values = {value: bodies.values[value] for value in dict.fromkeys(ids[start:end])}
            failed = any(row["status"] not in ("provided", "range_required") for row in values.values())
            partial = start or end < len(ids) or any(row["status"] == "range_required" for row in values.values())
            return {"status": "incomplete" if failed else "partial" if partial else "complete",
                    "object_ids": ids[start:end], "bodies": values, "start": start, "end": end,
                    "total": len(ids), "content_sha256": digest, "next_start": end if end < len(ids) else None,
                    "next_command": command("batch", "--start=" + str(end), "--content-sha256=" + digest) if end < len(ids) else None,
                    "blob_command": command("blob", "<object_id>"),
                    "instructions": "Repeat identical full-ID stdin for subsequent pages. Only provided bodies are supplied; recover missing ones individually. " + GUIDANCE}
        result = output()
        while not fits(result) and end - start > 1:
            end -= 1
            result = output()
        if not fits(result) and end > start:
            bodies.values[ids[start]] = {"status": "range_required"}
            result = output()
        return require_fit(result)
    if action == "blob":
        values = [oid(args.object_id)]
        arguments = ["cat-file", "blob", *values]
        identity = {"operation": action, "object_id": values[0]}
    elif action == "diff":
        values = [oid(args.before), oid(args.after)]
        arguments = ["diff", "--no-ext-diff", "--no-textconv", *values, "--"]
        identity = {"operation": action, "before": values[0], "after": values[1]}
    elif action in ("dependency", "directory"):
        revision = oid(args.revision)
        if args.path or action == "dependency":
            validate_artifact_path(args.path)
        values = [revision, "--path=" + args.path]
        spec = revision + ":./" + args.path
        arguments = (["show", spec] if action == "dependency" else
                     ["-c", "core.quotePath=false", "ls-tree", "--full-tree", "--no-abbrev", spec, "--"])
        identity = {"operation": action, "revision": revision, "path": args.path}
    else:
        files._fail("handoff_invalid_arguments")
    return git_range(repo, arguments, identity,
                     lambda offset, digest: command(action, *values, "--start=" + str(offset), "--content-sha256=" + digest),
                     start=start, expected=args.content_sha256)
