"""Fixed-tree local commit adapter; never stages or writes working-tree content.

Object creation and ref publication are separate effects. The orchestrator must
persist the candidate ID before publication and reconcile an uncertain publish
by observing that exact ref/commit, never by creating another candidate.
"""

from contextlib import contextmanager
import os
from pathlib import Path
import subprocess
import time

from .completion import FULL_GIT_OBJECT_ID, resolve_git_commit, safe_git_command, safe_git_environment
from .git_snapshot import (
    capture_git_snapshot, manifest_fingerprint, parse_index_entries, run_git_bytes,
    verify_git_snapshot_commit,
)
from .review_wait_runtime.review_wait_repository import _path, _physical, _identity


class FinalizationGitError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _fail(code="finalization_git_failed"):
    raise FinalizationGitError(code)


def branch(repo):
    value = run_git_bytes(repo, ["symbolic-ref", "--quiet", "HEAD"], output_limit=4096).decode("utf-8").rstrip("\n")
    if not value.startswith("refs/heads/") or any(ord(c) < 32 for c in value):
        _fail("finalization_git_unsupported")
    return value


def matching_snapshot(repo, *, base, fingerprint, expected_branch):
    before = branch(repo)
    snapshot = capture_git_snapshot(repo)
    if (before != expected_branch or branch(repo) != before
            or snapshot.base_revision != base or snapshot.fingerprint != fingerprint):
        _fail("review_target_mismatch")
    return snapshot


def _write(repo, arguments, raw=None):
    """Only this module's fixed plumbing operations reach this private adapter."""
    try:
        result = subprocess.run(
            [*safe_git_command(repo), "-c", "core.fsmonitor=false", "-c", "core.hooksPath=" + os.devnull, *arguments],
            input=raw, stdin=subprocess.DEVNULL if raw is None else None,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, shell=False,
            timeout=15, env=safe_git_environment(), check=False,
        )
    except (OSError, subprocess.SubprocessError):
        _fail("finalization_git_outcome_unknown")
    if result.returncode != 0:
        _fail()
    return result.stdout


def _object_id(raw):
    value = raw.decode("ascii").strip()
    if not FULL_GIT_OBJECT_ID.fullmatch(value):
        _fail("finalization_git_outcome_unknown")
    return value


def create_candidate(repo, *, base, fingerprint, expected_branch, task_id):
    """Create immutable objects from the reviewed entries, leaving refs untouched.

    Nested governed directories cannot safely publish a whole-repository tree
    from their project-relative snapshot. They keep the existing manual route.
    """
    if run_git_bytes(repo, ["rev-parse", "--show-prefix"], output_limit=4096).strip():
        _fail("finalization_git_unsupported")
    matching_snapshot(repo, base=base, fingerprint=fingerprint, expected_branch=expected_branch)
    entries = parse_index_entries(run_git_bytes(
        repo, ["-c", "core.fsmonitor=false", "ls-files", "--cached", "--stage", "--sparse", "-z", "--"]))
    if manifest_fingerprint(base, entries) != fingerprint:
        _fail("review_target_mismatch")
    directories = {(): {}}
    for entry in entries:
        parts = tuple(entry.path.split(b"/"))
        if any(part in (b"", b".", b"..") for part in parts):
            _fail("finalization_git_unsupported")
        for size in range(1, len(parts)):
            directories.setdefault(parts[:size], {})
        kind = b"commit" if entry.mode == b"160000" else b"blob"
        directories[parts[:-1]][parts[-1]] = (entry.mode, kind, entry.object_id)
    for parts in sorted(directories, key=len, reverse=True):
        raw = b"".join(mode + b" " + kind + b" " + oid + b"\t" + name + b"\0"
                       for name, (mode, kind, oid) in sorted(directories[parts].items()))
        tree = _object_id(_write(repo, ["mktree", "-z"], raw))
        if parts:
            directories[parts[:-1]][parts[-1]] = (b"040000", b"tree", tree.encode("ascii"))
    # No hooks, signing, editor, filter, arbitrary message or command execution.
    # Project rules requiring them use the explicit manual completion path.
    candidate = _object_id(_write(repo, ["-c", "commit.gpgSign=false", "commit-tree", tree, "-p", base],
                                  ("Complete reviewed Task " + task_id + "\n").encode("ascii")))
    verify_git_snapshot_commit(repo, candidate, expected_base_revision=base, expected_fingerprint=fingerprint)
    matching_snapshot(repo, base=base, fingerprint=fingerprint, expected_branch=expected_branch)
    return candidate


@contextmanager
def _git_locks(repo):
    """Exclude index writers; Git's ref transaction owns HEAD and branch locks."""
    owned = []
    try:
        for name in ("index",):
            raw = run_git_bytes(repo, ["rev-parse", "--path-format=absolute", "--git-path", name], output_limit=16384)
            path = _path(Path(os.fsdecode(raw.rstrip(b"\n")) + ".lock"))
            try:
                descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0), 0o600)
            except FileExistsError:
                _fail("finalization_git_busy")
            try:
                identity = _identity(os.fstat(descriptor))
                owned.append((path, identity))
            finally:
                os.close(descriptor)
        head = run_git_bytes(repo, ["rev-parse", "--path-format=absolute", "--git-path", "HEAD"], output_limit=16384)
        if os.path.lexists(os.fsdecode(head.rstrip(b"\n")) + ".lock"):
            _fail("finalization_git_busy")
        yield
    finally:
        for path, identity in reversed(owned):
            details = path.lstat()
            if not _physical(details) or _identity(details) != identity:
                _fail("finalization_git_outcome_unknown")
            path.unlink()


def published(repo, *, candidate, base, fingerprint, expected_branch):
    """Observe publication; a different ref or HEAD never authorizes replay."""
    verify_git_snapshot_commit(repo, candidate, expected_base_revision=base, expected_fingerprint=fingerprint)
    if branch(repo) != expected_branch:
        _fail("review_target_mismatch")
    return resolve_git_commit(repo, "HEAD") == candidate and resolve_git_commit(repo, expected_branch) == candidate


def _publish_transaction(repo, *, candidate, base, expected_branch, revalidate):
    """Prepare Git's own locks, inspect HEAD while held, then commit once.

    Git implicitly locks/logs HEAD when updating its current referent; an
    additional symref-verify on HEAD would be a duplicate update. Ref backends
    without the observable physical HEAD lock retain the manual route.
    """
    process = None
    try:
        process = subprocess.Popen([*safe_git_command(repo), "-c", "core.hooksPath=" + os.devnull,
            "update-ref", "--stdin", "--no-deref", "-m", "taskgov reviewed completion"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            bufsize=0, env=safe_git_environment(), shell=False)
        os.set_blocking(process.stdout.fileno(), False)
        def exchange(raw, expected):
            process.stdin.write(raw)
            process.stdin.flush()
            output = bytearray()
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                try:
                    chunk = os.read(process.stdout.fileno(), 1024)
                except BlockingIOError:
                    chunk = None
                if chunk:
                    output.extend(chunk)
                    if bytes(output) == expected:
                        return
                    if not expected.startswith(output):
                        _fail("finalization_git_outcome_unknown")
                elif chunk == b"" or process.poll() is not None:
                    _fail()
                time.sleep(0.005)
            _fail("finalization_git_outcome_unknown")
        exchange(("start\nupdate " + expected_branch + " " + candidate + " " + base
                  + "\nprepare\n").encode("utf-8"), b"start: ok\nprepare: ok\n")
        head = run_git_bytes(repo, ["rev-parse", "--path-format=absolute", "--git-path", "HEAD"], output_limit=16384)
        lock = _path(Path(os.fsdecode(head.rstrip(b"\n")) + ".lock"))
        if not _physical(lock.lstat()) or branch(repo) != expected_branch:
            _fail("review_target_mismatch")
        revalidate()
        exchange(b"commit\n", b"commit: ok\n")
        process.stdin.close()
        if process.wait(timeout=15) != 0:
            _fail("finalization_git_outcome_unknown")
    except (OSError, subprocess.SubprocessError):
        _fail("finalization_git_outcome_unknown")
    finally:
        if process is not None:
            if process.stdin is not None and not process.stdin.closed:
                process.stdin.close()  # EOF aborts an uncommitted transaction.
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
            process.stdout.close()


def publish_candidate(repo, *, candidate, base, fingerprint, expected_branch, revalidate=lambda: None):
    """CAS the existing branch after exact checks, then verify the actual result.

    The caller persists publishing intent first. Failure or lost response must
    be reconciled through published(), not an automatic update-ref retry.
    """
    verify_git_snapshot_commit(repo, candidate, expected_base_revision=base, expected_fingerprint=fingerprint)
    with _git_locks(repo):
        matching_snapshot(repo, base=base, fingerprint=fingerprint, expected_branch=expected_branch)
        revalidate()
        matching_snapshot(repo, base=base, fingerprint=fingerprint, expected_branch=expected_branch)
        _publish_transaction(repo, candidate=candidate, base=base, expected_branch=expected_branch,
                             revalidate=revalidate)
        if not published(repo, candidate=candidate, base=base, fingerprint=fingerprint, expected_branch=expected_branch):
            _fail("finalization_git_outcome_unknown")
    return candidate
