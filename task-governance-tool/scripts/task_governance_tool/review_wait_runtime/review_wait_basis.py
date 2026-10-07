"""Source-only current-basis provider through one fixed public read-only helper.

This module never opens Task storage, reads saved controller values as current,
or supplies executor identity. The admitted parent ID is compared with the
stored owner returned by the helper. Command injection exists for offline tests.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from uuid import UUID

from task_governance_tool.review_wait_runtime.review_wait_controller import WaitBinding


MAX_RESPONSE_BYTES = 16_384
_FIELDS = {"version", "project_id", "project_path_hash", "project_binding_generation",
           "task_id", "task_status", "execution_id", "ownership_generation", "parent_thread_id",
           "contract_revision", "target_kind", "target_value", "target_base_revision",
           "target_generation", "artifact_manifest_id"}


class BasisError(ValueError):
    def __init__(self, code="wait_basis_unavailable"):
        self.code = code
        super().__init__(code)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise BasisError("wait_basis_response_invalid")
        result[key] = value
    return result


def parse_basis(raw: bytes, *, wait_id: str, task_id: str, parent_thread_id: str) -> WaitBinding:
    """Accept only a complete success from the fixed helper, without prose."""
    try:
        if type(raw) is not bytes or len(raw) > MAX_RESPONSE_BYTES:
            raise ValueError()
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if (type(value) is not dict or set(value) != {"ok", "status", "basis"}
                or value["ok"] is not True or value["status"] != "review_wait_basis"):
            raise ValueError()
        basis = value["basis"]
        if (type(basis) is not dict or set(basis) != _FIELDS
                or type(basis["version"]) is not int or basis["version"] != 1
                or basis["task_status"] not in ("in_progress", "review_pending")
                or basis["task_id"] != task_id or basis["parent_thread_id"] != parent_thread_id
                or str(UUID(parent_thread_id)) != parent_thread_id
                or basis["target_kind"] not in
                   ("git_snapshot", "git_commit", "diff_fingerprint", "external_revision")
                or not re.fullmatch(r"tg_execution_[0-9a-f]{16}", basis["execution_id"])
                or not re.fullmatch(r"tg_artifact_manifest_[0-9a-f]{16}", basis["artifact_manifest_id"])):
            raise ValueError()
        kind, target, base = (basis["target_kind"], basis["target_value"], basis["target_base_revision"])
        if kind in ("git_snapshot", "diff_fingerprint") and not re.fullmatch(r"sha256:[0-9a-f]{64}", target):
            raise ValueError()
        commit = base if kind == "git_snapshot" else target
        if kind in ("git_snapshot", "git_commit") and (
                not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", commit) or set(commit) == {"0"}):
            raise ValueError()
        if kind != "git_snapshot" and base != "":
            raise ValueError()
        return WaitBinding(wait_id=wait_id, **{key: item for key, item in basis.items()
                                             if key not in ("version", "task_status")})
    except Exception:
        raise BasisError("wait_basis_response_invalid") from None


def _capture(command, *, cwd, timeout_seconds, cleanup_seconds):
    """Bound output, time and direct-child lifetime; stderr is never retained."""
    process = None
    captured = bytearray()
    succeeded = False
    try:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, cwd=cwd, shell=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        os.set_blocking(process.stdout.fileno(), False)
        deadline = time.monotonic() + timeout_seconds
        ended = False
        while time.monotonic() < deadline:
            try:
                chunk = os.read(process.stdout.fileno(), 8192)
            except BlockingIOError:
                chunk = None
            if chunk:
                captured.extend(chunk)
                if len(captured) > MAX_RESPONSE_BYTES:
                    raise BasisError()
            elif chunk == b"":
                ended = True
            if ended and process.poll() is not None:
                succeeded = process.returncode == 0
                break
            if not chunk:
                time.sleep(0.005)
    except Exception:
        succeeded = False
    finally:
        if process is not None:
            try:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=cleanup_seconds)
            except Exception:
                succeeded = False
            finally:
                try:
                    process.stdout.close()
                except Exception:
                    succeeded = False
    if not succeeded:
        raise BasisError()
    return bytes(captured)


class PublicTaskBasisReader:
    """Callable fresh original-Task reader; no cached or caller-supplied basis.

    The default helper is the one physical project installation. Development
    self-host callers pass their admitted source helper explicitly. This neither
    selects another state path nor initializes absent state. The helper owns
    package/project scope and canonical resolver admission.
    """

    def __init__(self, repo, task_id, parent_thread_id, wait_id, *, helper=None,
                 test_command=None, timeout_seconds=15.0, cleanup_seconds=1.0):
        try:
            if (type(task_id) is not str or not task_id or len(task_id) > 128
                    or type(wait_id) is not str or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/+-]{0,127}", wait_id)
                    or type(parent_thread_id) is not str or str(UUID(parent_thread_id)) != parent_thread_id
                    or isinstance(timeout_seconds, bool) or not math.isfinite(timeout_seconds)
                    or not 0 < timeout_seconds <= 30 or isinstance(cleanup_seconds, bool)
                    or not math.isfinite(cleanup_seconds) or not 0 < cleanup_seconds <= 1):
                raise ValueError()
            self.repo = Path(repo).absolute()
            entry = (Path(helper).absolute() if helper is not None else
                     self.repo / ".agents/skills/task-governance-tool/scripts/review_handoff.py")
            command = (sys.executable, "-I", "-B", str(entry))
            if test_command is not None:
                if (not isinstance(test_command, (list, tuple)) or not test_command
                        or any(type(part) is not str or not part for part in test_command)):
                    raise ValueError()
                command = tuple(test_command)
            self.command = (*command, "wait-basis", "--repo=" + str(self.repo), "--task-id=" + task_id)
            self.task_id, self.parent_thread_id, self.wait_id = task_id, parent_thread_id, wait_id
            self.timeout_seconds, self.cleanup_seconds = timeout_seconds, cleanup_seconds
        except Exception:
            raise BasisError("wait_basis_invalid_arguments") from None

    def read(self) -> WaitBinding:
        raw = _capture(self.command, cwd=self.repo, timeout_seconds=self.timeout_seconds,
                       cleanup_seconds=self.cleanup_seconds)
        return parse_basis(raw, wait_id=self.wait_id, task_id=self.task_id,
                           parent_thread_id=self.parent_thread_id)

    def __call__(self) -> WaitBinding:
        return self.read()
