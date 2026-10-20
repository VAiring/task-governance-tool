"""Transient exact-target material delivery; no state, relevance inference or writes."""

from __future__ import annotations

import hashlib
import json

from task_governance_tool import review_handoff_transport as files
from task_governance_tool.artifact_manifest import (
    ARTIFACT_MANIFEST_BYTE_LIMIT, ArtifactManifestError, build_artifact_entries, decode_artifact_path,
    validate_artifact_path,
)
from task_governance_tool.git_snapshot import GitSnapshotError, run_git_stream, stream_tree_entries
from task_governance_tool.review_delivery import TEXT_BUDGET, GUIDANCE, encode, fits, require_fit

BODY_BYTE_LIMIT = TEXT_BUDGET
TOTAL_BYTE_LIMIT = TEXT_BUDGET


class _TooLarge(Exception):
    pass


class _Bodies:
    def __init__(self, repo):
        self.repo = repo
        self.remaining = TOTAL_BYTE_LIMIT
        self.patch_remaining = TOTAL_BYTE_LIMIT
        self.values = {}
        self.patches = {}
        self._patch_ids = {}
        self._patch_pairs = {}

    def text(self, arguments):
        limit = BODY_BYTE_LIMIT
        payload = bytearray()

        def append(chunk):
            if len(payload) + len(chunk) > limit:
                raise _TooLarge
            payload.extend(chunk)

        try:
            run_git_stream(self.repo, arguments, append)
        except _TooLarge:
            return {"status": "range_required"}
        except GitSnapshotError:
            return {"status": "unavailable"}
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError:
            return {"status": "non_text"}
        if "\0" in text:
            return {"status": "non_text"}
        return {"status": "provided", "byte_count": len(payload), "text": text}

    def patch(self, before, after):
        pair = (before, after)
        if pair not in self._patch_pairs:
            body = self.text(["diff", "--no-ext-diff", "--no-textconv", before, after, "--"])
            if body["status"] == "provided":
                # Compare complete text, including headers and line endings. The
                # table and equality index reference the same retained string.
                patch_id = self._patch_ids.get(body["text"])
                if patch_id is None:
                    if body["byte_count"] > self.patch_remaining:
                        self._patch_pairs[pair] = {"format": "git_patch", "status": "range_required"}
                        return self._patch_pairs[pair]
                    patch_id = f"p{len(self.patches) + 1}"
                    self._patch_ids[body["text"]] = patch_id
                    self.patches[patch_id] = body
                    self.patch_remaining -= body["byte_count"]
                body = {"status": "provided", "patch_id": patch_id}
            self._patch_pairs[pair] = {"format": "git_patch", **body}
        return self._patch_pairs[pair]

    def source(self, path, revision, revision_kind, side, mode, object_id):
        row = {"path": path, "revision": revision, "revision_kind": revision_kind,
               "side": side, "mode": mode, "object_id": object_id, "body_id": None}
        if mode == "160000":
            return {**row, "status": "submodule_unavailable"}
        self.blob(object_id)
        return {**row, "body_id": object_id, "status": self.values[object_id]["status"]}

    def blob(self, object_id):
        if object_id not in self.values:
            body = self.text(["cat-file", "blob", object_id])
            if body["status"] == "provided":
                if body["byte_count"] > self.remaining:
                    body = {"status": "range_required"}
                else:
                    self.remaining -= body["byte_count"]
            self.values[object_id] = body
        return self.values[object_id]


def _diff(bodies, before, after):
    sides = [side for side in (before, after) if side is not None]
    if any(side["mode"] == "160000" for side in sides):
        return {"status": "source_unavailable"}
    if before is not None and after is not None:
        return bodies.patch(before["object_id"], after["object_id"])
    # A full addition/deletion is exactly the supplied body plus its operation.
    # Refer to it, without manufacturing an empty Git object or duplicating text.
    return {"status": sides[0]["status"], "format": "whole_file",
            "operation": "add" if after is not None else "delete", "body_id": sides[0]["body_id"]}


def collect_material(repo, packet_path, packet_sha256, stream, *, start=0, selection_sha256=None, records_only=False):
    """Read only reviewer-selected paths plus every changed side/diff, once per blob."""
    from task_governance_tool.review_handoff_preparation import _observe_material, _material_command

    path = files.checked_path(repo, packet_path)
    raw = files.read_bounded(path, files.PACKET_LIMIT)
    if hashlib.sha256(raw).hexdigest() != packet_sha256:
        files.fail("review_packet_stale")
    packet = files.decode_packet(raw)
    selected_raw = stream.read(ARTIFACT_MANIFEST_BYTE_LIMIT + 1)
    if len(selected_raw) > ARTIFACT_MANIFEST_BYTE_LIMIT:
        files.fail("handoff_invalid_arguments")
    try:
        selected = json.loads(selected_raw.decode("utf-8"))
        if not isinstance(selected, list):
            files.fail("handoff_invalid_arguments")
        for item in selected:
            validate_artifact_path(item)
    except (ArtifactManifestError, UnicodeError, ValueError, TypeError):
        files.fail("handoff_invalid_arguments")
    selected = list(dict.fromkeys(selected))
    selection_digest = hashlib.sha256(encode(selected)).hexdigest()
    if selection_sha256 is not None and selection_digest != selection_sha256:
        files.fail("review_material_range_mismatch")
    target = packet["review_target"]
    observed = _observe_material(repo, target)
    entries = build_artifact_entries(observed.before_leaves, observed.after_leaves)
    total = len(entries) + len(selected)
    if start < 0 or start > total:
        files.fail("handoff_invalid_arguments")
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
            files.fail(exc.code)
    bodies = _Bodies(repo)
    changes = []
    # Retain a bounded page of complete mappings before attempting any content.
    stop = min(start + 20, total)
    for entry in entries[start:min(stop, len(entries))]:
        before = (bodies.source(entry.old_path, observed.comparison_base, "git_treeish", "before",
                               entry.before_mode, entry.before_object_id) if entry.old_path is not None else None)
        after = (bodies.source(entry.new_path, target["value"], target["kind"], "after",
                              entry.after_mode, entry.after_object_id) if entry.new_path is not None else None)
        changes.append({"ordinal": entry.ordinal, "kind": entry.kind,
                        "before": before, "after": after, "diff": _diff(bodies, before, after)})
    dependencies = []
    for selected_path in selected[max(0, start - len(entries)):max(0, stop - len(entries))]:
        leaf = after_leaves.get(selected_path)
        if leaf is None:
            dependencies.append({"path": selected_path, "revision": target["value"],
                                 "revision_kind": target["kind"], "side": "dependency",
                                 "mode": None, "object_id": None, "body_id": None, "status": "absent_at_target"})
        else:
            dependencies.append(bodies.source(
                selected_path, target["value"] if selected_path in changed else revision,
                target["kind"] if selected_path in changed else "git_commit", "dependency", *leaf))
    if files.read_bounded(path, files.PACKET_LIMIT) != raw:
        files.fail("handoff_file_changed")
    command = lambda action, *args: _material_command(repo, action, *args, records_only=records_only)

    def result():
        sources = [*dependencies, *(row[key] for row in changes for key in ("before", "after") if row[key] is not None)]
        diffs = [row["diff"] for row in changes]
        body_ids = {row["body_id"] for row in sources if row["body_id"] is not None}
        patch_ids = {row["patch_id"] for row in diffs if "patch_id" in row}
        end = start + len(changes) + len(dependencies)
        complete = all(row["status"] == "provided" for row in sources + diffs)
        failed = any(row["status"] not in ("provided", "range_required") for row in sources + diffs)
        return {"status": "incomplete" if failed else "complete" if complete and start == 0 and end == total else "partial",
                "review_target": target, "packet_sha256": packet_sha256, "selection_sha256": selection_digest,
                "start": start, "end": end, "total": total, "remaining": total - end,
                "changes_total": len(entries), "dependencies_total": len(selected),
                "next_start": end if end < total else None,
                "next_command": command("collect", "--packet=" + packet_path, "--packet-sha256=" + packet_sha256,
                    "--selection-sha256=" + selection_digest, "--start=" + str(end)) if end < total else None,
                "changes": changes, "dependencies": dependencies,
                "bodies": {key: bodies.values[key] for key in sorted(body_ids)},
                "patches": {key: bodies.patches[key] for key in sorted(patch_ids)},
                "blob_command": command("blob", "<object_id>"),
                "diff_command": command("diff", "<before_object_id>", "<after_object_id>"),
                "instructions": "Repeat exactly the same selected-path JSON stdin for next_command. Inspect every changed entry and complete diff; every change precedes selected dependencies in stable order. Recover missing required before/after context and dependencies by object_id, and every two-sided diff by before/after IDs. Whole-file add/delete diffs require their referenced body. Missing unrelated full bodies alone do not block review; missing required context or diffs must be recovered or reported before PASS. Mode 120000 is link text; 160000 requires supplied material. No successful sibling needs repetition. " + GUIDANCE}

    output = result()
    while not fits(output) and len(changes) + len(dependencies) > 1:
        (dependencies if dependencies else changes).pop()
        output = result()
    if not fits(output):
        # A single entry can have two independently admissible text groups but
        # exceed the combined transport. Keep its selectors and split the reads.
        for value in bodies.values.values():
            if value["status"] == "provided":
                value.clear()
                value["status"] = "range_required"
        bodies.patches.clear()
        for row in changes:
            for side in (row["before"], row["after"]):
                if side is not None and side["status"] == "provided":
                    side["status"] = "range_required"
            if row["diff"]["status"] == "provided":
                row["diff"].pop("patch_id", None)
                row["diff"]["status"] = "range_required"
        for row in dependencies:
            if row["status"] == "provided":
                row["status"] = "range_required"
        output = result()
    return require_fit(output)
