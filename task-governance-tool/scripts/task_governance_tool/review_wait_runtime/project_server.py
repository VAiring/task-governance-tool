"""Project-scoped MCP controls for canonical, one-shot review waits.

Discovery is inert. Explicit prepare owns only operational-state creation;
Setup owns opt-in and the host owns configuration, trust and reservations.
No worker is recovered or retried when the process starts or reads old state.
"""

from dataclasses import asdict, dataclass
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
from .managed_host import ManagedHost
from .managed_direct import ManagedDirectProbe
from .managed_wait import ManagedWait
from . import review_wait_mcp_relay as protocol


_ID = {"type": "string"}
OPERATIONS = {
    "wait": {"task_id": _ID, "reviewer_ids": {"type": "array", "minItems": 1,
        "maxItems": 64, "uniqueItems": True, "items": {"type": "string"}}},
    "inspect": {"task_id": _ID},
    "stop": {"task_id": _ID},
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
                 session_factory=None, clock=None, managed_host_factory=None):
        if type(config) is not ProjectConfig or any(
                not path.is_absolute() for path in (config.repo, config.server_path, config.codex_home)):
            raise ValueError("invalid_configuration")
        self.config = config
        self.skill = Path(__file__).absolute().parents[3]
        self.host_factory = host_factory or ProjectHost.from_environment
        self.managed_host_factory = managed_host_factory or ManagedHost.from_environment
        self.basis_factory = basis_factory or PublicTaskBasisReader
        self.session_factory = session_factory or ReviewWaitSession
        self.clock = clock
        self.sessions = {}
        self.closed = False
        self.managed = ManagedWait(self)

    @staticmethod
    def catalogue():
        descriptions = {
            "wait": "Wait for the original Task and actual returned reviewers using the authorized connected service. Creates and starts its same-parent timer; end the turn only on ok=true,status=waiting,parent_may_end=true. A matching prepare-finalization intent runs fixed registration, gates, local commit and completion before notification. Otherwise it notifies review end only. Sending is not PASS or Task completion. Follow references/review_wait.md#normal-wait for waiting and references/task_workflow.md#continue-after-reviews for result processing; no separate ACK or routine status call.",
            "inspect": "Optional read-only diagnosis of this parent's Task wait; never starts or retries effects.",
            "stop": "Explicitly stop this parent's Task wait and clean up only known effects; no unknown-operation retry.",
            "prepare": "Compatibility only: prepare the earlier per-reservation flow with an authorized PAUSED same-parent timer; no timer or send effect. Normal waiting uses review_wait_wait.",
            "view": "Compatibility/recovery only: inspect an earlier per-reservation wait without starting observation or host effects. Normal diagnosis uses review_wait_inspect.",
            "direct_delete_start": "Compatibility only: start an earlier prepared wait, delete its timer and send once to this same idle parent when its exact reviewers end. Requires send authorization. Normal waiting uses review_wait_wait.",
            "direct_status": "Compatibility/recovery only: inspect an earlier wait's timer, send and receipt; never retries. Normal diagnosis uses review_wait_inspect.",
            "direct_cancel": "Compatibility/recovery only: cancel an earlier wait and clean up known-ACTIVE state; never retry unknown effects. Normal cancellation uses review_wait_stop.",
            "direct_ack": "Compatibility only: acknowledge an earlier wait in a genuinely new turn of this same parent, without review evidence. Normal waiting records receipt automatically.",
        }
        return [{"name": "review_wait_" + operation, "description": descriptions[operation],
                 "inputSchema": {"type": "object", "properties": fields,
                                 "required": list(fields), "additionalProperties": False},
                 "annotations": {"readOnlyHint": operation in {"view", "direct_status", "inspect"}}}
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

    def _finalizer(self, binding):
        if not self._enabled():
            raise BasisError()
        paths = self._location(enabling=False)
        intent = paths.review_finalization_store(binding.parent_thread_id,
                                                 binding.task_id, binding.target_generation)
        if not path_lexically_exists(intent):
            return None
        from task_governance_tool.review_finalization import Finalizer
        from task_governance_tool.session_identity import CallerIdentity
        finalizer = Finalizer(self.config.repo, binding.task_id, CallerIdentity(binding.parent_thread_id),
                              generation=binding.target_generation)
        basis = finalizer.journal.read().basis
        if any(basis.get(key) != value for key, value in asdict(binding).items() if key != "wait_id"):
            raise BasisError()
        return finalizer

    def _session(self, path, task_id, automation_id, *, managed=False, prepare_reviewer_reader=None):
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
        options = {}
        host_factory = self.host_factory
        if managed:
            host_factory = lambda **kwargs: self.managed_host_factory(task_id=task_id, **kwargs)
            options["prepare_reviewer_reader"] = prepare_reviewer_reader
            options["direct_factory"] = lambda *args, **kwargs: ManagedDirectProbe(
                *args, finalization_factory=lambda controller: self._finalizer(controller.binding), **kwargs)
        session = self.session_factory(config, host_factory=host_factory,
            basis_factory=self._reader, clock=self.clock, **options)
        self.sessions[automation_id] = session
        return session

    def _ensure_root(self, paths):
        if not path_lexically_exists(paths.review_wait_root):
            create_physical_directory_exclusive(paths.review_wait_root, root=paths.fixed_root)
        inspect_physical_directory(paths.review_wait_root, root=paths.fixed_root)

    def handle(self, operation, arguments, metadata):
        try:
            if self.closed:
                return {"ok": False, "error": "service_closed"}
            if (operation not in OPERATIONS or type(arguments) is not dict
                    or set(arguments) != set(OPERATIONS[operation])):
                return {"ok": False, "error": "invalid_request"}
            actual = admit_executor_metadata(metadata)
            enabling = operation in {"wait", "prepare", "direct_delete_start"}
            if enabling:
                executor_turn_id(actual)
                if not self._enabled():
                    return {"ok": False, "error": "review_wait_not_enabled"}
            paths = self._location(enabling=enabling)
            if operation in {"wait", "inspect", "stop"}:
                validate_task_id(arguments["task_id"])
                return self.managed.handle(operation, arguments, actual, paths)
            automation = arguments["automation_id"]
            path = paths.review_wait_store(automation)
            if operation == "prepare":
                task_id = validate_task_id(arguments["task_id"])
                # Validate live ownership before creating even an empty directory.
                self._reader(self.config.repo, task_id, actual["threadId"], "prepare",
                             helper=self.skill / "scripts/review_handoff.py")()
                self._ensure_root(paths)
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
