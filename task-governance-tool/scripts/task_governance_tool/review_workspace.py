"""Optional exact Git material restoration for an independently chosen check.

No target-code execution, arbitrary output path, Runner admission, DB, or log.
The OS temporary workspace is disposable material, not a security sandbox.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import stat
import subprocess
import time
from dataclasses import asdict
from pathlib import Path

from task_governance_tool import review_handoff_transport as files
from task_governance_tool.reviews import ReviewEvidenceError
from task_governance_tool.task_values import TaskValidationError
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
LOCATION_LIMIT = 8192


def workspace_guidance(repo, packet_path, raw, *, details=False):
    """Return a caller template; select temporary storage only when it runs."""
    if not details:
        return {"status": "optional", "instructions": (
            "Only when independently selected, already-authorized verification needs files, use "
            "review_material.recovery_command for complete workspace preparation and cleanup details. "
            "Run its caller template in one host invocation; this option grants no execution permission and adds no required test or review gate."
        )}
    from task_governance_tool.review_workspace_caller import caller_template
    return {
        "status": "template_ready", "caller_language": "python",
        "caller_code": caller_template(repo, packet_path, hashlib.sha256(raw).hexdigest()),
        "instructions": (
            "Optional: when your independently selected, already-authorized verification needs files, "
            "use this Python caller template in ONE host invocation, including location selection, preparation, "
            "your verification and cleanup. Supply only your selected existing argv/timeout and its known "
            "process-lifetime condition; the helper never executes verification. Do not split selection and "
            "preparation across tool calls: a host-scoped temporary parent may disappear between them. Do not "
            "reconstruct source/tests or Git-fetch scripts. Prepare restores the complete fixed target "
            "without executing it, installing dependencies or granting trust. Read project rules first. "
            "This is disposable material, not a security sandbox. Inspect your own actual result; ready, "
            "another reviewer's PASS, missing/incomplete output, failure or timeout is not your PASS. "
            "The template prints the retained cleanup command before creation. Keep it until all processes "
            "using the workspace have ended. A direct child exit alone is not proof about descendants; "
            "timeout, interruption or unknown process lifetime retains the copy for inspected recovery. "
            "Its saved physical location stays fixed across temporary-environment changes. Do not edit "
            "the generated location or substitute a destination. A missing or inaccessible original parent "
            "is a limitation, never permission to relocate. "
            "Lost prepare output: use the retained cleanup command, never replay "
            "prepare blindly. Lost whole invocation/cleanup output leaves an unknown outcome. "
            "not_started does not prove absence or safe retry. Cleanup uncertainty requires inspection; do not delete an uncertain tree. "
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
                files.fail("review_workspace_temp_unavailable")
            configured = str(Path(os.environ["LOCALAPPDATA"]) / "Temp")
        else:
            configured = "/tmp"
    if not Path(configured).is_absolute():
        files.fail("review_workspace_temp_unavailable")
    try:
        # OS defaults may use aliases (for example /var on macOS). Resolve
        # only this location selector, before admission or any write. All
        # subsequent operations use the validated physical path, never alias
        # traversal; a retargeted alias must still match the retained digest.
        parent = Path(configured).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise files.HandoffError("review_workspace_temp_unavailable") from exc
    _validate_parent(repo, parent)
    return parent


def _validate_parent(repo, parent):
    # The selected physical root and every ancestor must remain directories
    # outside Git; this does not relax source/Packet or restored-entry checks.
    for path in (parent, *parent.parents):
        inspect_physical_directory(path)
        if path_lexically_exists(path / ".git"):
            files.fail("review_workspace_temp_unavailable")
    if parent == repo or repo in parent.parents:
        files.fail("review_workspace_temp_unavailable")
    return inspect_physical_directory(parent)


def _location_binding(repo, parent, packet_digest, identity):
    """Retain a read-admitted physical location, not an arbitrary output option."""
    selected = _validate_parent(repo, parent)
    value = {"version": 1, "parent": str(parent),
             "parent_identity": asdict(selected.identity),
             "repo_sha256": _path_digest(repo), "packet_sha256": packet_digest,
             "workspace_id": identity}
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    encoded = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    if len(encoded) > LOCATION_LIMIT:
        files.fail("review_workspace_temp_unavailable")
    return encoded


def _saved_parent(repo, encoded, packet_digest, identity):
    if (not isinstance(encoded, str) or not 0 < len(encoded) <= LOCATION_LIMIT
            or re.fullmatch(r"[A-Za-z0-9_-]+", encoded) is None):
        files.fail("review_workspace_invalid_binding")
    try:
        raw = base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
        value = json.loads(raw)
        canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    except (ValueError, TypeError, UnicodeError, binascii.Error, RecursionError):
        files.fail("review_workspace_invalid_binding")
    if (type(value) is not dict or set(value) != {
            "version", "parent", "parent_identity", "repo_sha256", "packet_sha256", "workspace_id"}
            or type(value["version"]) is not int or value["version"] != 1
            or base64.urlsafe_b64encode(canonical).decode("ascii").rstrip("=") != encoded
            or value["repo_sha256"] != _path_digest(repo)
            or value["packet_sha256"] != packet_digest or value["workspace_id"] != identity):
        files.fail("review_workspace_invalid_binding")
    physical = value["parent_identity"]
    if (type(physical) is not dict or set(physical) != {"device", "inode"}
            or any(type(item) is not int or item < 0 for item in physical.values())
            or type(value["parent"]) is not str or "\x00" in value["parent"]):
        files.fail("review_workspace_invalid_binding")
    parent = Path(value["parent"])
    if not parent.is_absolute() or str(Path(os.path.abspath(parent))) != value["parent"]:
        files.fail("review_workspace_invalid_binding")
    selected = _validate_parent(repo, parent)
    if asdict(selected.identity) != physical:
        files.fail("review_workspace_temp_changed")
    return selected


def _location(repo, identity, digest, *, location=None, packet_digest=None):
    if re.fullmatch(r"[0-9a-f]{32}", identity) is None:
        files.fail("review_workspace_invalid_id")
    if location is not None:
        if digest is not None:
            files.fail("review_workspace_invalid_binding")
        selected = _saved_parent(repo, location, packet_digest, identity)
    else:
        # Older generated commands retained only a path digest. Never guess
        # their original location or weaken their environment comparison.
        parent = _temporary_parent(repo)
        if digest != _path_digest(parent):
            files.fail("review_workspace_temp_changed")
        selected = _validate_parent(repo, parent)
    return selected, selected.path / ("taskgov-review-" + identity)


def _packet(repo, path, digest):
    if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        files.fail("review_workspace_invalid_binding")
    selected = files.checked_path(repo, path)
    raw = files.read_bounded(selected, files.PACKET_LIMIT)
    if hashlib.sha256(raw).hexdigest() != digest:
        files.fail("review_workspace_packet_changed")
    return selected, raw, files.decode_packet(raw)


def _owner(repo, packet, digest, identity, directory):
    return {"version": 1, "workspace_id": identity,
            "repo_digest": _path_digest(repo),
            "packet_sha256": digest, "task_id": packet["task"]["task_id"],
            "contract_revision": packet["contract"]["revision"],
            "review_target": packet["review_target"],
            "directory_identity": asdict(directory.identity)}


def _check_root(directory, parent):
    if inspect_physical_directory(directory.path, root=parent) != directory:
        files.fail("review_workspace_changed")


def _remove(directory, parent, marker=None, *, selected_parent=None):
    """Inventory before deletion; never follow links or recursively delete paths."""
    deadline = time.monotonic() + CLEANUP_SECONDS
    pending = [(directory.path, 0)]
    selected_files, selected_dirs = [], []
    count = 0
    parents = files.observe_parents(directory.path)

    def check():
        if time.monotonic() > deadline:
            files.fail("review_workspace_cleanup_timeout")
        files.check_parents(parents)
        if selected_parent is not None and inspect_physical_directory(parent) != selected_parent:
            files.fail("review_workspace_temp_changed")
        _check_root(directory, parent)

    while pending:
        check()
        path, depth = pending.pop()
        if depth > CLEANUP_DEPTH:
            files.fail("review_workspace_cleanup_limit")
        observed = inspect_physical_directory(path, root=parent)
        selected_dirs.append((depth, observed))
        with os.scandir(path) as children:
            for child in children:
                check()
                count += 1
                if count > CLEANUP_ENTRIES:
                    files.fail("review_workspace_cleanup_limit")
                details = child.stat(follow_symlinks=False)
                if (stat.S_ISLNK(details.st_mode) or getattr(details, "st_file_attributes", 0)
                        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)):
                    files.fail("review_workspace_cleanup_unsafe")
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
                files.fail("review_workspace_changed")
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
        files.fail("review_workspace_cleanup_uncertain")


def _observation(repo, packet):
    from task_governance_tool.git_snapshot import run_git_bytes
    from task_governance_tool.verification_runner_git import (
        observe_commit_runner_target, observe_staged_runner_target,
    )
    # This optional materializer's supported layout remains the Git root;
    # nested manual review/completion support does not expand its admission.
    prefix = run_git_bytes(repo, ["rev-parse", "--show-prefix"], output_limit=4096)
    if prefix != b"\n":
        files.fail("review_workspace_nested_git_unsupported")
    target = packet["review_target"]
    if target["kind"] == "git_snapshot":
        observed = observe_staged_runner_target(repo)
    elif target["kind"] == "git_commit":
        observed = observe_commit_runner_target(repo, target["value"])
    else:
        files.fail("review_workspace_target_unsupported")
    artifact = observed.artifact
    if (artifact.target_kind, artifact.target_value, artifact.target_base_revision) != (
            target["kind"], target["value"], target["base_revision"]):
        files.fail("review_target_mismatch")
    if any(part.casefold() in {".git", "git~1"}
           for entry in observed.entries for part in entry.relative_posix_path.split("/")):
        files.fail("review_workspace_git_marker_unsupported")
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
                files.fail("review_workspace_changed")
            os.fchmod(descriptor, 0o700)
            if inspect_physical_file(path, root=target)[1] != identity:
                files.fail("review_workspace_changed")
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
    cleanup_status = "unknown" if args.workspace_operation == "cleanup" else "not_started"
    stage = "packet"
    try:
        path, raw, packet = _packet(repo, args.packet, args.packet_sha256)
        stage = "location"
        selected_parent, root = _location(repo, args.workspace_id, getattr(args, "temp_root_sha256", None),
            location=getattr(args, "workspace_location", None), packet_digest=args.packet_sha256)
        parent = selected_parent.path
        if args.workspace_operation == "cleanup":
            stage = "cleanup"
            if not path_lexically_exists(root):
                if _validate_parent(repo, parent) != selected_parent:
                    files.fail("review_workspace_temp_changed")
                return {"ok": True, "status": "absent", "workspace_id": args.workspace_id}
            cleanup_status = "retained"
            directory = inspect_physical_directory(root, root=parent)
            marker, _ = read_physical_file_bounded(root / MARKER, root=parent, max_bytes=MARKER_LIMIT)
            expected = _owner(repo, packet, args.packet_sha256, args.workspace_id, directory)
            if json.loads(marker) != expected:
                files.fail("review_workspace_owner_mismatch")
            _remove(directory, parent, marker, selected_parent=selected_parent)
            return {"ok": True, "status": "absent", "workspace_id": args.workspace_id}
        stage = "creation"
        if path_lexically_exists(root):
            cleanup_status = "retained"
            files.fail("review_workspace_exists")
        stage = "live_before"
        validate_live_packet(repo, path, raw, packet)
        stage = "material"
        observed = _observation(repo, packet)
        material = preflight_runner_material(repo, observed)
        stage = "location"
        parents = files.observe_parents(root)
        files.check_parents(parents)
        if inspect_physical_directory(parent) != selected_parent:
            files.fail("review_workspace_temp_changed")
        # New private root only; never rewrite permissions of existing paths.
        stage = "creation"
        root.mkdir() if os.name == "nt" else root.mkdir(mode=0o700)
        cleanup_status = "retained"
        stage = "identity"
        created = inspect_physical_directory(root, root=parent)
        files.check_parents(parents)
        marker = json.dumps(_owner(repo, packet, args.packet_sha256, args.workspace_id, created),
                            separators=(",", ":")).encode("utf-8")
        create_exclusive_durable_file(root / MARKER, marker, root=parent, max_bytes=MARKER_LIMIT)
        stage = "restoration"
        target = create_physical_directory_exclusive(root / "target", root=parent)
        restored = materialize_runner_target(repo, material, target.path)
        _executable_modes(target.path, observed.entries)
        stage = "live_after"
        validate_live_packet(repo, path, raw, packet)
        stage = "identity"
        files.check_parents(parents)
        _check_root(created, parent)
        actual_marker, _ = read_physical_file_bounded(root / MARKER, root=parent, max_bytes=MARKER_LIMIT)
        if actual_marker != marker:
            files.fail("review_workspace_changed")
        if inspect_physical_directory(target.path, root=parent) != target:
            files.fail("review_workspace_changed")
        return {"ok": True, "status": "ready", "workspace_id": args.workspace_id,
                "working_directory": str(target.path), "task_id": packet["task"]["task_id"],
                "contract_revision": packet["contract"]["revision"],
                "review_target": packet["review_target"], "material": asdict(restored),
                "verification_status": "not_run", "cleanup_status": "required"}
    except (files.HandoffError, StatePathError, VerificationRunnerGitError,
            GitSnapshotError, ReviewEvidenceError, TaskValidationError,
            OSError, ValueError, KeyError, TypeError, RecursionError,
            subprocess.SubprocessError, KeyboardInterrupt) as exc:
        code = exc.code if isinstance(exc, (files.HandoffError, VerificationRunnerGitError,
            GitSnapshotError, ReviewEvidenceError, TaskValidationError)) else "review_workspace_unavailable"
        from task_governance_tool.review_workspace_diagnostics import diagnostic
        failure = diagnostic(stage, exc)
        if isinstance(exc, KeyboardInterrupt):
            code = "review_workspace_interrupted"
            cleanup_status = "retained" if cleanup_status == "retained" else "unknown"
        elif created is not None:
            try:
                _remove(created, parent, selected_parent=selected_parent)
                cleanup_status = "absent"
            except KeyboardInterrupt as interrupted:
                code = "review_workspace_interrupted"
                failure = diagnostic("cleanup", interrupted)
                cleanup_status = "retained"
            except (files.HandoffError, StatePathError, OSError, ValueError):
                cleanup_status = "retained"
        return {"ok": False, "status": "unavailable", "code": code,
                "workspace_id": (args.workspace_id if re.fullmatch(r"[0-9a-f]{32}", args.workspace_id) else None),
                "cleanup_status": cleanup_status,
                "diagnostic": failure,
                "message": "No verification result. Recover the reported binding/material or inspect retained workspace before another preparation; use retained cleanup only after its processes end."}
