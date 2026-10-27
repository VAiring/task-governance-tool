"""One-invocation caller support; no verifier, callback, process or log owner."""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

from . import review_workspace as workspace
from .review_workspace_diagnostics import diagnostic


class Workspace:
    """Select once in the caller's host invocation and retain exact cleanup."""

    def __init__(self, repo, packet, packet_sha256):
        self.repo = Path(os.path.abspath(repo))
        self.args = None
        self.cleanup_command = None
        self._attempted = False
        self._preparation = None
        identity = None
        try:
            identity = uuid.uuid4().hex
            parent = workspace._temporary_parent(self.repo)
            location = workspace._location_binding(self.repo, parent, packet_sha256, identity)
            from .review_handoff_preparation import _shell
            entry = Path(__file__).parent.parent / "review_handoff.py"
            cleanup_command = _shell([sys.executable, "-B", str(entry), "workspace", "cleanup",
                "--repo=" + str(self.repo), "--packet=" + packet, "--packet-sha256=" + packet_sha256,
                "--workspace-id=" + identity, "--workspace-location=" + location])
        except KeyboardInterrupt:
            self._preparation = {
                "ok": False, "status": "unavailable", "code": "review_workspace_interrupted",
                "workspace_id": identity, "cleanup_status": "unknown",
                "diagnostic": {"stage": "selection", "reason": "unavailable"},
                "message": "No verification result. Selection was interrupted; inspect uncertainty before recovery.",
            }
            return
        except (workspace.files.HandoffError, workspace.StatePathError, OSError) as exc:
            self._preparation = {
                "ok": False, "status": "unavailable", "code": "review_workspace_temp_unavailable",
                "workspace_id": identity, "cleanup_status": "not_started",
                "diagnostic": diagnostic("selection", exc),
                "message": "No verification result. Inspect the original temporary parent; do not relocate or blindly retry.",
            }
            return
        self.args = SimpleNamespace(workspace_operation="prepare", packet=packet,
            packet_sha256=packet_sha256, workspace_id=identity, workspace_location=location,
            temp_root_sha256=None)
        self.cleanup_command = cleanup_command

    def prepare(self):
        if self._attempted:
            return self._refused("preparation_already_attempted")
        self._attempted = True
        if self.args is not None:
            self._preparation = None  # Interrupted/undelivered operation is unknown.
            self._preparation = workspace.operate(self.repo, self.args)
        return self._preparation

    def _refused(self, reason):
        observed = (self._preparation or {}).get("cleanup_status", "unknown")
        return {"ok": False, "status": "unavailable", "code": "review_workspace_caller_refused",
            "workspace_id": self.args.workspace_id if self.args is not None else None,
            "cleanup_status": "retained" if observed == "required" else observed,
            "diagnostic": {"stage": "caller", "reason": reason},
            "message": "No verification result. Confirm all using processes ended and inspect uncertainty before recovery."}

    def cleanup(self, *, processes_ended):
        # Caller evidence only: the helper does not supervise processes or
        # infer descendants' state from an exit code, timeout or exception.
        if processes_ended is not True:
            return self._refused("process_end_unconfirmed")
        if self.args is None:
            return self._preparation
        args = SimpleNamespace(**{**vars(self.args), "workspace_operation": "cleanup"})
        self._preparation = None  # A lost cleanup acknowledgement is unknown.
        result = workspace.operate(self.repo, args)
        self._preparation = {"cleanup_status": "absent" if result.get("status") == "absent"
                             else result.get("cleanup_status", "unknown")}
        return result


def caller_template(repo, packet, packet_sha256):
    """Literal Python caller scaffold; never evaluate the caller's selections."""
    scripts = str(Path(__file__).parent.parent)
    return f'''import json, subprocess, sys
sys.dont_write_bytecode = True
sys.path.insert(0, {scripts!r})
from task_governance_tool.review_workspace_caller import Workspace

# Fill from your independently selected, already-authorized existing check.
selected_argv = []
selected_timeout_seconds = None
# True only with evidence that this command joins EVERY using process on
# normal return (including nonzero return). A child exit alone is insufficient.
normal_return_confirms_all_processes_ended = False
if not selected_argv:
    raise SystemExit("Supply your selected existing verification argv before running this template.")

# Keep this entire flow in ONE host/tool invocation; do not retain a selected
# host-scoped temporary parent for preparation in a later invocation.
copy = Workspace({str(repo)!r}, {packet!r}, {packet_sha256!r})
print(json.dumps({{"workspace_cleanup_command": copy.cleanup_command}}), flush=True)
prepared = copy.prepare()
print(json.dumps({{"workspace_preparation": prepared}}), flush=True)
if prepared.get("ok") is True and prepared.get("status") == "ready":
    all_processes_ended = False
    try:
        # Verification belongs to this caller, never to the workspace helper.
        # Inspect actual output yourself. No Receipt or PASS is inferred.
        checked = subprocess.run(selected_argv, cwd=prepared["working_directory"],
                                 timeout=selected_timeout_seconds, check=False, shell=False)
        all_processes_ended = normal_return_confirms_all_processes_ended is True
        print(json.dumps({{"verification_returncode": checked.returncode}}), flush=True)
    except subprocess.TimeoutExpired:
        # run() ends its direct child; descendants may still use the copy.
        print(json.dumps({{"verification_status": "timeout"}}), flush=True)
    except KeyboardInterrupt:
        print(json.dumps({{"verification_status": "interrupted"}}), flush=True)
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        print(json.dumps({{"verification_status": "unavailable"}}), flush=True)
    finally:
        print(json.dumps({{"workspace_cleanup": copy.cleanup(
            processes_ended=all_processes_ended)}}), flush=True)
# On preparation failure, inspect its diagnostic/cleanup_status. not_started
# is not absence. Do not blindly prepare again or relocate a saved binding.
# On timeout/interruption/unconfirmed process end, inspect and use the exact
# retained cleanup command only after ALL using processes have ended.
'''
