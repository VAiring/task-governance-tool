"""Transient exact-target material delivery; no state, relevance inference or writes."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict

from task_governance_tool import review_handoff as files
from task_governance_tool.artifact_manifest import (
    ARTIFACT_MANIFEST_BYTE_LIMIT, ArtifactManifestError, build_artifact_entries, decode_artifact_path,
    validate_artifact_path,
)
from task_governance_tool.git_snapshot import GitSnapshotError, run_git_stream, stream_tree_entries

BODY_BYTE_LIMIT = 1024 * 1024
TOTAL_BYTE_LIMIT = 4 * 1024 * 1024


class _TooLarge(Exception):
    pass


class _Bodies:
    def __init__(self, repo):
        self.repo = repo
        self.remaining = TOTAL_BYTE_LIMIT
        self.values = {}

    def text(self, arguments):
        limit = min(BODY_BYTE_LIMIT, self.remaining)
        payload = bytearray()

        def append(chunk):
            if len(payload) + len(chunk) > limit:
                raise _TooLarge
            payload.extend(chunk)

        try:
            run_git_stream(self.repo, arguments, append)
        except _TooLarge:
            return {"status": "too_large" if limit == BODY_BYTE_LIMIT else "delivery_limit"}
        except GitSnapshotError:
            return {"status": "unavailable"}
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError:
            return {"status": "non_text"}
        if "\0" in text:
            return {"status": "non_text"}
        self.remaining -= len(payload)
        return {"status": "provided", "byte_count": len(payload), "text": text}

    def source(self, path, revision, revision_kind, side, mode, object_id):
        row = {"path": path, "revision": revision, "revision_kind": revision_kind,
               "side": side, "mode": mode, "object_id": object_id, "body_id": None}
        if mode == "160000":
            return {**row, "status": "submodule_unavailable"}
        if object_id not in self.values:
            self.values[object_id] = self.text(["cat-file", "blob", object_id])
        return {**row, "body_id": object_id, "status": self.values[object_id]["status"]}


def _diff(bodies, before, after):
    sides = [side for side in (before, after) if side is not None]
    if any(side["status"] != "provided" for side in sides):
        return {"status": "source_unavailable"}
    if before is not None and after is not None:
        return {"format": "git_patch", **bodies.text([
            "diff", "--no-ext-diff", "--no-textconv", before["object_id"], after["object_id"], "--"])}
    # A full addition/deletion is exactly the supplied body plus its operation.
    # Refer to it, without manufacturing an empty Git object or duplicating text.
    return {"status": "provided", "format": "whole_file",
            "operation": "add" if after is not None else "delete", "body_id": sides[0]["body_id"]}


def collect_material(repo, packet_path, packet_sha256, stream):
    """Read only reviewer-selected paths plus every changed side/diff, once per blob."""
    from task_governance_tool.review_handoff_preparation import _observe_material

    path = files._path(repo, packet_path)
    raw = files._read(path, files.PACKET_LIMIT)
    if hashlib.sha256(raw).hexdigest() != packet_sha256:
        files._fail("review_packet_stale")
    packet = files._packet(raw)
    selected_raw = stream.read(ARTIFACT_MANIFEST_BYTE_LIMIT + 1)
    if len(selected_raw) > ARTIFACT_MANIFEST_BYTE_LIMIT:
        files._fail("handoff_invalid_arguments")
    try:
        selected = json.loads(selected_raw.decode("utf-8"))
        if not isinstance(selected, list):
            files._fail("handoff_invalid_arguments")
        for item in selected:
            validate_artifact_path(item)
    except (ArtifactManifestError, UnicodeError, ValueError, TypeError):
        files._fail("handoff_invalid_arguments")
    target = packet["review_target"]
    observed = _observe_material(repo, target)
    entries = build_artifact_entries(observed.before_leaves, observed.after_leaves)
    after_leaves = {leaf.relative_posix_path: (leaf.mode, leaf.object_id) for leaf in observed.after_leaves}
    changed = {entry.new_path for entry in entries if entry.new_path is not None}
    changed_paths = {name for entry in entries for name in (entry.old_path, entry.new_path) if name is not None}
    revision = target["base_revision"] if target["kind"] == "git_snapshot" else target["value"]
    unchanged = set(selected) - changed_paths
    if unchanged:
        def dependency(entry):
            name = decode_artifact_path(entry.path)
            if name in unchanged:
                after_leaves[name] = (entry.mode.decode("ascii"), entry.object_id.decode("ascii"))
        try:
            stream_tree_entries(repo, revision, object_id_length=len(revision), consume_entry=dependency)
        except (ArtifactManifestError, GitSnapshotError) as exc:
            files._fail(exc.code)
    bodies = _Bodies(repo)
    changes = []
    for entry in entries:
        before = (bodies.source(entry.old_path, observed.comparison_base, "git_treeish", "before",
                               entry.before_mode, entry.before_object_id) if entry.old_path is not None else None)
        after = (bodies.source(entry.new_path, target["value"], target["kind"], "after",
                              entry.after_mode, entry.after_object_id) if entry.new_path is not None else None)
        changes.append({**asdict(entry), "before": before, "after": after, "diff": _diff(bodies, before, after)})
    dependencies = []
    for selected_path in dict.fromkeys(selected):
        leaf = after_leaves.get(selected_path)
        if leaf is None:
            dependencies.append({"path": selected_path, "revision": target["value"],
                                 "revision_kind": target["kind"], "side": "dependency",
                                 "mode": None, "object_id": None, "body_id": None, "status": "absent_at_target"})
        else:
            dependencies.append(bodies.source(
                selected_path, target["value"] if selected_path in changed else revision,
                target["kind"] if selected_path in changed else "git_commit", "dependency", *leaf))
    if files._read(path, files.PACKET_LIMIT) != raw:
        files._fail("handoff_file_changed")
    rows = [*dependencies, *(row["diff"] for row in changes),
            *(row[key] for row in changes for key in ("before", "after") if row[key] is not None)]
    return {"status": "complete" if all(row["status"] == "provided" for row in rows) else "incomplete",
            "review_target": target, "changes": changes, "dependencies": dependencies, "bodies": bodies.values,
            "instructions": ["Only provided bodies/diffs are supplied. Read their complete text and reuse it for authority checks and review with the exact path/revision/side mappings; another purpose does not require another copy. Do not treat missing, mismatched, unknown or tool-truncated text as already read. Use the first read's recovery_command (or its already supplied individual commands) when individual/additional reads or discovery are needed; recover only affected material and follow new dependencies without collecting successful siblings again. This is material delivery, not review coverage or PASS."]}
