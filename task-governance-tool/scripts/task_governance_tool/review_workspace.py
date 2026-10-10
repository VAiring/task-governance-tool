"""Optional exact Git material restoration for an independently chosen check.

No target-code execution, arbitrary output path, Runner admission, DB, or log.
The OS temporary workspace is disposable material, not a security sandbox.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import sys
import time
import uuid
from dataclasses import asdict
from pathlib import Path

from task_governance_tool import review_handoff as files
from task_governance_tool.state_paths import (
    StatePathError, ValidatedFile, create_exclusive_durable_file,
    create_physical_directory_exclusive, inspect_physical_directory,
    inspect_physical_file, path_lexically_exists, read_physical_file_bounded,
    rmdir_validated_directory, unlink_validated_file,
)

MARKER = "owner.json"
MARKER_LIMIT = 4096
CLEANUP_SECONDS = 30
CLEANUP_ENTRIES = 60000
CLEANUP_DEPTH = 66


def workspace_guidance(repo, packet_path, raw, *, details=False):
    """Defer optional lifecycle instructions and handle allocation until needed."""
    if not details:
        return {"status": "optional", "instructions": (
            "Only when independently selected, already-authorized verification needs files, use "
            "review_material.recovery_command for complete workspace preparation and cleanup details. "
            "Availability is checked there; this option grants no execution permission and adds no required test or review gate."
        )}
    from task_governance_tool.review_handoff_preparation import _shell
    identity = uuid.uuid4().hex
    try:
        parent = _temporary_parent(repo)
    except (files.HandoffError, StatePathError, OSError):
        return {"status": "unavailable", "code": "review_workspace_temp_unavailable",
                "instructions": "Optional verification workspace unavailable; retain existing material retrieval and execution permissions."}
    entry = Path(__file__).parent.parent / "review_handoff.py"
    common = ["--repo=" + str(repo), "--packet=" + packet_path,
              "--packet-sha256=" + hashlib.sha256(raw).hexdigest(),
              "--workspace-id=" + identity, "--temp-root-sha256=" + _path_digest(parent)]
    return {
        "status": "available",
        "prepare_command": _shell([sys.executable, "-B", str(entry), "workspace", "prepare", *common]),
        "cleanup_command": _shell([sys.executable, "-B", str(entry), "workspace", "cleanup", *common]),
        "instructions": (
            "Optional: when your independently selected, already-authorized verification needs files, "
            "prepare once and run the existing approved command in returned working_directory; do not "
            "reconstruct source/tests or Git-fetch scripts. Prepare restores the complete fixed target "
            "without executing it, installing dependencies or granting trust. Read project rules first. "
            "This is disposable material, not a security sandbox. Inspect your own actual result; ready, "
            "another reviewer's PASS, missing/incomplete output, failure or timeout is not your PASS. "
            "Keep these commands until all processes using the workspace have ended, then cleanup, also "
            "after failure/timeout. Lost prepare output: use the retained cleanup command, never replay "
            "prepare blindly. Cleanup uncertainty requires inspection; do not delete an uncertain tree. "
            "No mandatory test, new review gate, or parent argument is introduced."
        ),
    }


def _path_digest(path):
    return hashlib.sha256(os.path.normcase(str(path)).encode("utf-8")).hexdigest()


def _temporary_parent(repo):
    # tempfile.gettempdir() may probe candidates by writing before validation.
    # Select the configured OS temporary path read-only and fail closed instead.
    configured = next((os.environ[name] for name in ("TMPDIR", "TEMP", "TMP")
                       if os.environ.get(name)), None)
    if configured is None:
        if os.name == "nt":
            if not os.environ.get("LOCALAPPDATA"):
                files._fail("review_workspace_temp_unavailable")
            configured = str(Path(os.environ["LOCALAPPDATA"]) / "Temp")
        else:
            configured = "/tmp"
    if not Path(configured).is_absolute():
        files._fail("review_workspace_temp_unavailable")
    try:
        # OS defaults may use aliases (for example /var on macOS). Resolve
        # only this location selector, before admission or any write. All
        # subsequent operations use the validated physical path, never alias
        # traversal; a retargeted alias must still match the retained digest.
        parent = Path(configured).resolve(strict=True)
    except (OSError, RuntimeError):
        files._fail("review_workspace_temp_unavailable")
    # The selected physical root and every ancestor must remain directories
    # outside Git; this does not relax source/Packet or restored-entry checks.
    for path in (parent, *parent.parents):
        inspect_physical_directory(path)
        if path_lexically_exists(path / ".git"):
            files._fail("review_workspace_temp_unavailable")
    if parent == repo or repo in parent.parents:
        files._fail("review_workspace_temp_unavailable")
    return parent


def _location(repo, identity, digest):
    if re.fullmatch(r"[0-9a-f]{32}", identity) is None:
        files._fail("review_workspace_invalid_id")
    parent = _temporary_parent(repo)
    if digest != _path_digest(parent):
        files._fail("review_workspace_temp_changed")
    return parent, parent / ("taskgov-review-" + identity)


def _packet(repo, path, digest):
    if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        files._fail("review_workspace_invalid_binding")
    selected = files._path(repo, path)
    raw = files._read(selected, files.PACKET_LIMIT)
    if hashlib.sha256(raw).hexdigest() != digest:
        files._fail("review_workspace_packet_changed")
    return selected, raw, files._packet(raw)


def _owner(repo, packet, digest, identity, directory):
    return {"version": 1, "workspace_id": identity,
            "repo_digest": _path_digest(repo),
            "packet_sha256": digest, "task_id": packet["task"]["task_id"],
            "contract_revision": packet["contract"]["revision"],
            "review_target": packet["review_target"],
            "directory_identity": asdict(directory.identity)}


def _check_root(directory, parent):
    if inspect_physical_directory(directory.path, root=parent) != directory:
        files._fail("review_workspace_changed")


def _remove(directory, parent, marker=None):
    """Inventory before deletion; never follow links or recursively delete paths."""
    deadline = time.monotonic() + CLEANUP_SECONDS
    pending = [(directory.path, 0)]
    selected_files, selected_dirs = [], []
    count = 0
    parents = files._parents(directory.path)

    def check():
        if time.monotonic() > deadline:
            files._fail("review_workspace_cleanup_timeout")
        files._check_parents(parents)
        _check_root(directory, parent)

    while pending:
        check()
        path, depth = pending.pop()
        if depth > CLEANUP_DEPTH:
            files._fail("review_workspace_cleanup_limit")
        observed = inspect_physical_directory(path, root=parent)
        selected_dirs.append((depth, observed))
        with os.scandir(path) as children:
            for child in children:
                check()
                count += 1
                if count > CLEANUP_ENTRIES:
                    files._fail("review_workspace_cleanup_limit")
                details = child.stat(follow_symlinks=False)
                if (stat.S_ISLNK(details.st_mode) or getattr(details, "st_file_attributes", 0)
                        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)):
                    files._fail("review_workspace_cleanup_unsafe")
                child_path = Path(child.path)
                if stat.S_ISDIR(details.st_mode):
                    pending.append((child_path, depth + 1))
                else:
                    _, physical = inspect_physical_file(child_path, root=parent)
                    selected_files.append(ValidatedFile(child_path, physical, ""))
    # Keep ownership proof until all target material is gone; a partial cleanup
    # can be inspected and retried without inventing or broadening ownership.
    marker_files = [item for item in selected_files if item.path == directory.path / MARKER]
    for item in selected_files:
        if item in marker_files:
            continue
        check()
        if marker is not None:
            current, _ = read_physical_file_bounded(directory.path / MARKER, root=parent, max_bytes=MARKER_LIMIT)
            if current != marker:
                files._fail("review_workspace_changed")
        unlink_validated_file(item, root=parent)
    for _, item in sorted(selected_dirs, key=lambda pair: -pair[0]):
        if item.path == directory.path:
            continue
        check()
        rmdir_validated_directory(item, root=parent)
    for item in marker_files:
        check()
        unlink_validated_file(item, root=parent)
    check()
    rmdir_validated_directory(directory, root=parent)
    if path_lexically_exists(directory.path):
        files._fail("review_workspace_cleanup_uncertain")


def _observation(repo, packet):
    from task_governance_tool.git_snapshot import run_git_bytes
    from task_governance_tool.verification_runner_git import (
        observe_commit_runner_target, observe_staged_runner_target,
    )
    # Existing index and commit observers use different Git path projections
    # beneath an enclosing worktree. Admit only identical root coordinates.
    prefix = run_git_bytes(repo, ["rev-parse", "--show-prefix"], output_limit=4096)
    if prefix != b"\n":
        files._fail("review_workspace_nested_git_unsupported")
    target = packet["review_target"]
    if target["kind"] == "git_snapshot":
        observed = observe_staged_runner_target(repo)
    elif target["kind"] == "git_commit":
        observed = observe_commit_runner_target(repo, target["value"])
    else:
        files._fail("review_workspace_target_unsupported")
    artifact = observed.artifact
    if (artifact.target_kind, artifact.target_value, artifact.target_base_revision) != (
            target["kind"], target["value"], target["base_revision"]):
        files._fail("review_target_mismatch")
    if any(part.casefold() in {".git", "git~1"}
           for entry in observed.entries for part in entry.relative_posix_path.split("/")):
        files._fail("review_workspace_git_marker_unsupported")
    return observed


def _executable_modes(target, entries):
    """Preserve tracked executability only in the new private POSIX copy."""
    if os.name == "nt":
        return
    for entry in entries:
        if entry.mode != "100755":
            continue
        path = target / entry.relative_posix_path
        _, identity = inspect_physical_file(path, root=target)
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            opened = os.fstat(descriptor)
            if (opened.st_dev, opened.st_ino, opened.st_size) != (identity.device, identity.inode, identity.size):
                files._fail("review_workspace_changed")
            os.fchmod(descriptor, 0o700)
            if inspect_physical_file(path, root=target)[1] != identity:
                files._fail("review_workspace_changed")
        finally:
            os.close(descriptor)


def operate(repo, args):
    """Return closed lifecycle diagnostics, never a verification result/Receipt."""
    from task_governance_tool.review_handoff_preparation import validate_live_packet
    from task_governance_tool.verification_runner_git import (
        VerificationRunnerGitError, preflight_runner_material, materialize_runner_target,
    )
    from task_governance_tool.git_snapshot import GitSnapshotError
    created = None
    parent = root = None
    cleanup_status = "retained" if args.workspace_operation == "cleanup" else "not_started"
    try:
        path, raw, packet = _packet(repo, args.packet, args.packet_sha256)
        parent, root = _location(repo, args.workspace_id, args.temp_root_sha256)
        if args.workspace_operation == "cleanup":
            if not path_lexically_exists(root):
                return {"ok": True, "status": "absent", "workspace_id": args.workspace_id}
            cleanup_status = "retained"
            directory = inspect_physical_directory(root, root=parent)
            marker, _ = read_physical_file_bounded(root / MARKER, root=parent, max_bytes=MARKER_LIMIT)
            expected = _owner(repo, packet, args.packet_sha256, args.workspace_id, directory)
            if json.loads(marker) != expected:
                files._fail("review_workspace_owner_mismatch")
            _remove(directory, parent, marker)
            return {"ok": True, "status": "absent", "workspace_id": args.workspace_id}
        if path_lexically_exists(root):
            cleanup_status = "retained"
            files._fail("review_workspace_exists")
        validate_live_packet(repo, path, raw, packet)
        observed = _observation(repo, packet)
        material = preflight_runner_material(repo, observed)
        parents = files._parents(root)
        files._check_parents(parents)
        # New private root only; never rewrite permissions of existing paths.
        root.mkdir() if os.name == "nt" else root.mkdir(mode=0o700)
        cleanup_status = "retained"
        created = inspect_physical_directory(root, root=parent)
        files._check_parents(parents)
        marker = json.dumps(_owner(repo, packet, args.packet_sha256, args.workspace_id, created),
                            separators=(",", ":")).encode("utf-8")
        create_exclusive_durable_file(root / MARKER, marker, root=parent, max_bytes=MARKER_LIMIT)
        target = create_physical_directory_exclusive(root / "target", root=parent)
        restored = materialize_runner_target(repo, material, target.path)
        _executable_modes(target.path, observed.entries)
        validate_live_packet(repo, path, raw, packet)
        files._check_parents(parents)
        _check_root(created, parent)
        actual_marker, _ = read_physical_file_bounded(root / MARKER, root=parent, max_bytes=MARKER_LIMIT)
        if actual_marker != marker:
            files._fail("review_workspace_changed")
        if inspect_physical_directory(target.path, root=parent) != target:
            files._fail("review_workspace_changed")
        return {"ok": True, "status": "ready", "workspace_id": args.workspace_id,
                "working_directory": str(target.path), "task_id": packet["task"]["task_id"],
                "contract_revision": packet["contract"]["revision"],
                "review_target": packet["review_target"], "material": asdict(restored),
                "verification_status": "not_run", "cleanup_status": "required"}
    except (files.HandoffError, StatePathError, VerificationRunnerGitError,
            GitSnapshotError, OSError, ValueError, KeyError, TypeError) as exc:
        code = exc.code if isinstance(exc, (files.HandoffError, VerificationRunnerGitError, GitSnapshotError)) else "review_workspace_unavailable"
        if created is not None:
            try:
                _remove(created, parent)
                cleanup_status = "absent"
            except (files.HandoffError, StatePathError, OSError, ValueError):
                cleanup_status = "retained"
        return {"ok": False, "status": "unavailable", "code": code,
                "workspace_id": (args.workspace_id if re.fullmatch(r"[0-9a-f]{32}", args.workspace_id) else None),
                "cleanup_status": cleanup_status,
                "message": "No verification result. Recover the reported binding/material or inspect retained workspace before another preparation; use retained cleanup only after its processes end."}
