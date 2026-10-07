"""Project-scoped MCP controls for canonical, one-shot review waits.

Discovery is inert. Explicit prepare owns only operational-state creation;
Setup owns opt-in and the host owns configuration, trust and reservations.
No worker is recovered or retried when the process starts or reads old state.
"""

from dataclasses import dataclass
from pathlib import Path
import sys

from task_governance_tool.project_scope import inspect_project_scope, STRUCTURAL_CODES
from task_governance_tool.setup_feature_config import read_choices
from task_governance_tool.state_paths import (
    create_physical_directory_exclusive, inspect_physical_directory, path_lexically_exists,
)
from task_governance_tool.state_resolver import resolve_project_state
from task_governance_tool.task_values import validate_task_id
from .review_wait_basis import BasisError, PublicTaskBasisReader
from .review_wait_host import PublicMcpHost, admit_executor_metadata, executor_turn_id
from .review_wait_repository import ReviewWaitRepository
from .review_wait_server import ReviewWaitSession, SessionConfig, serve
from .review_wait_service import ServiceConfig
from . import review_wait_mcp_relay as protocol


_ID = {"type": "string"}
OPERATIONS = {
    "prepare": {"automation_id": _ID, "task_id": _ID,
                "reviewer_ids": {"type": "array", "minItems": 1, "maxItems": 64,
                                 "uniqueItems": True, "items": {"type": "string",
                                 "description": "Actual returned /root/... reviewer handle or canonical thread UUID; handles resolve from this parent's structured dispatch records."}}},
    "view": {"automation_id": _ID},
    "direct_delete_start": {"automation_id": _ID},
    "direct_status": {"automation_id": _ID},
    "direct_cancel": {"automation_id": _ID, "probe_id": _ID},
    "direct_ack": {"automation_id": _ID, "probe_id": _ID},
}


class ProjectHost(PublicMcpHost):
    def _direct_prompt(self, probe):
        return (
            "このチャットの独立レビューが終了し、待機予約の削除を確認しました。"
            f'review_wait_direct_ack(automation_id="{self.automation_id}", probe_id="{probe}") '
            "で受領を記録し、保存した元Task・Packetのレビュー原本を回収して既存手順を続けてください。"
            "この通知自体はPASSやTask完了の証拠ではありません。予約の再作成や通知の再送は不要です。"
        )


@dataclass(frozen=True)
class ProjectConfig:
    repo: Path
    server_path: Path
    codex_home: Path
    timezone: str


class ProjectReviewWaitSession:
    operations = OPERATIONS

    def __init__(self, config, *, host_factory=None, basis_factory=None,
                 session_factory=None, clock=None):
        if type(config) is not ProjectConfig or any(
                not path.is_absolute() for path in (config.repo, config.server_path, config.codex_home)):
            raise ValueError("invalid_configuration")
        self.config = config
        self.skill = Path(__file__).absolute().parents[3]
        self.host_factory = host_factory or ProjectHost.from_environment
        self.basis_factory = basis_factory or PublicTaskBasisReader
        self.session_factory = session_factory or ReviewWaitSession
        self.clock = clock
        self.sessions = {}
        self.closed = False

    @staticmethod
    def catalogue():
        descriptions = {
            "prepare": "Prepare canonical one-shot state for the current Task, actual reviewers and an authorized PAUSED same-parent reservation; no timer or send effect.",
            "view": "Inspect the original prepared wait without starting observation or host effects.",
            "direct_delete_start": "Arm a ten-minute check, then delete/confirm it and send once to this same idle parent when its exact reviewers end. Requires authorization for that send.",
            "direct_status": "Inspect timer, send and receipt state; restart never retries an operation.",
            "direct_cancel": "Cancel this wait and attempt only known-ACTIVE cleanup; never retry an unknown effect.",
            "direct_ack": "Record receipt in a genuinely new turn of this same parent; does not register review evidence.",
        }
        return [{"name": "review_wait_" + operation, "description": descriptions[operation],
                 "inputSchema": {"type": "object", "properties": fields,
                                 "required": list(fields), "additionalProperties": False},
                 "annotations": {"readOnlyHint": operation in {"view", "direct_status"}}}
                for operation, fields in OPERATIONS.items()]

    def _enabled(self):
        try:
            return read_choices(self.skill)[1].get("review_wait") is True
        except Exception:
            return False

    def _location(self, *, enabling):
        inspection = inspect_project_scope(repo=self.config.repo, repo_explicit=True,
            script_path=self.skill / "scripts/review_wait_server.py",
            include_runtime=False, include_package=False, include_ignore=enabling)
        codes = STRUCTURAL_CODES | ({"state_ignore_required"} if enabling else set())
        if inspection.first_issue(allowed_codes=codes) or inspection.scope is None:
            raise ValueError("project_unavailable")
        resolution = resolve_project_state(skill_root=inspection.scope.skill_root,
                                           repo=inspection.scope.canonical_repo)
        if (resolution.error_code or resolution.binding != "matching"
                or resolution.layout != "fixed_current_v1" or resolution.target is None):
            raise ValueError("project_unavailable")
        return resolution.paths

    def _reader(self, repo, task, parent, wait_id, **kwargs):
        reader = self.basis_factory(repo, task, parent, wait_id, **kwargs)
        def current():
            if not self._enabled():
                raise BasisError()
            return reader()
        return current

    def _session(self, path, task_id, automation_id):
        if automation_id in self.sessions:
            session = self.sessions[automation_id]
            if session.config.task_id != task_id:
                raise ValueError("binding_mismatch")
            return session
        if len(self.sessions) >= 64:
            raise ValueError("session_limit")
        service = ServiceConfig(path, self.config.server_path, self.config.codex_home,
                                self.config.timezone, "host_node_intl")
        config = SessionConfig(service, self.config.repo, task_id, automation_id,
                               self.skill / "scripts/review_handoff.py")
        session = self.session_factory(config, host_factory=self.host_factory,
            basis_factory=self._reader, clock=self.clock)
        self.sessions[automation_id] = session
        return session

    def handle(self, operation, arguments, metadata):
        try:
            if self.closed:
                return {"ok": False, "error": "service_closed"}
            if (operation not in OPERATIONS or type(arguments) is not dict
                    or set(arguments) != set(OPERATIONS[operation])):
                return {"ok": False, "error": "invalid_request"}
            actual = admit_executor_metadata(metadata)
            enabling = operation in {"prepare", "direct_delete_start"}
            if enabling:
                executor_turn_id(actual)
                if not self._enabled():
                    return {"ok": False, "error": "review_wait_not_enabled"}
            paths = self._location(enabling=enabling)
            automation = arguments["automation_id"]
            path = paths.review_wait_store(automation)
            if operation == "prepare":
                task_id = validate_task_id(arguments["task_id"])
                # Validate live ownership before creating even an empty directory.
                self._reader(self.config.repo, task_id, actual["threadId"], "prepare",
                             helper=self.skill / "scripts/review_handoff.py")()
                if not path_lexically_exists(paths.review_wait_root):
                    create_physical_directory_exclusive(paths.review_wait_root, root=paths.fixed_root)
                inspect_physical_directory(paths.review_wait_root, root=paths.fixed_root)
                session = self._session(path, task_id, automation)
                args = {"reviewer_ids": arguments["reviewer_ids"]}
            else:
                controller = ReviewWaitRepository.open_existing(path).read().controller
                if (controller.binding.parent_thread_id != actual["threadId"]
                        or controller.reservation.timer_id != automation):
                    return {"ok": False, "error": "caller_mismatch"}
                session = self._session(path, controller.binding.task_id, automation)
                args = {key: value for key, value in arguments.items() if key != "automation_id"}
            result = session.handle(operation, args, metadata)
            return {**result, "automation_id": automation}
        except Exception:
            return {"ok": False, "error": "review_wait_unavailable"}

    def close(self):
        self.closed = True
        succeeded = True
        for session in self.sessions.values():
            try:
                succeeded = session.close() == {"ok": True} and succeeded
            except Exception:
                succeeded = False
        return {"ok": True} if succeeded else {"ok": False, "error": "observer_cleanup_unknown"}


def main(argv=None):
    parser = protocol._ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--server", required=True)
    parser.add_argument("--codex-home", required=True)
    parser.add_argument("--timezone", required=True)
    try:
        args = parser.parse_args(argv)
        config = ProjectConfig(Path(args.repo), Path(args.server), Path(args.codex_home), args.timezone)
        return serve(sys.stdin.buffer, sys.stdout.buffer, ProjectReviewWaitSession(config))
    except (Exception, KeyboardInterrupt):
        sys.stderr.write("Review-wait server unavailable.\n")
        return 1
