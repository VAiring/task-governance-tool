"""Project-scoped MCP controls for canonical, one-shot review waits.

Discovery is inert. Explicit prepare owns only operational-state creation;
Setup owns opt-in and the host owns configuration, trust and reservations.
No worker is recovered or retried when the process starts or reads old state.
"""

from dataclasses import asdict, dataclass, replace
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
from .failure_diagnostics import CallDiagnostics, DiagnosticError, PROJECT_REASONS
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
                 session_factory=None, clock=None, managed_host_factory=None, runtime_identity=None):
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
        self.runtime_identity = runtime_identity

    @staticmethod
    def catalogue():
        descriptions = {
            "wait": "Wait for the original Task and actual returned reviewers using the authorized connected service. Creates and starts its same-parent timer; delegate waiting and end the turn on ok=true,status=waiting,parent_may_end=true. On failure, report the limitation and recovery condition before ordinary turn end; no automatic resumption is promised. A matching prepare-finalization intent runs fixed registration, gates, local commit and completion before notification; otherwise it notifies review end only. A received body matching the original Task/target generation with status=completed, successful stages, actual Task done and sufficient reporting facts without unresolved contradiction or action permits direct reporting without another guide read, routine query or replay. Include all Findings (also low/resolved), warnings, limitations and not_recorded work; these alone do not exclude success. Labels (including delivery.source_body=complete), correlation, ok=true, review end/PASS and send acceptance alone prove neither completion nor unseen content. Follow references/review_wait.md#normal-wait for waiting; other results, including incomplete, unknown, failed, conflicting or action-requiring reports, use references/task_workflow.md#continue-after-reviews. No separate ACK or routine status call; next-Task authority reads and existing gates still apply.",
            "inspect": "Optional read-only diagnosis of this parent's Task wait and runtime identity. observation distinguishes request phase, exact worker running/stopped/unknown, direct versus saved joined termination, timer/send/receipt and observation time. No saved start flag proves life or exit; never starts/retries effects or writes evidence. Old notification observations are not current state. No routine poll or completion gate.",
            "stop": "Explicitly stop this parent's Task wait and clean up only known effects; no unknown-operation retry. operation reports cleanup separately from observation of the same worker running/stopped/unknown. Successful cleanup survives a later observation failure; unknown observation alone requires no repeat stop. Saved joined termination remains readable after restart.",
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
        return read_choices(self.skill)[1].get("review_wait") is True

    def _policy_enabled(self):
        try:
            return self._enabled()
        except Exception:
            raise DiagnosticError("policy_unreadable") from None

    def _location(self, *, enabling):
        inspection = inspect_project_scope(repo=self.config.repo, repo_explicit=True,
            script_path=self.skill / "scripts/review_wait_server.py",
            include_runtime=False, include_package=False, include_ignore=enabling)
        codes = STRUCTURAL_CODES | ({"state_ignore_required"} if enabling else set())
        issue = inspection.first_issue(allowed_codes=codes)
        if issue or inspection.scope is None:
            raise DiagnosticError(issue.code if issue and issue.code in PROJECT_REASONS else "project_unavailable")
        resolution = resolve_project_state(skill_root=inspection.scope.skill_root,
                                           repo=inspection.scope.canonical_repo)
        if (resolution.error_code or resolution.binding != "matching"
                or resolution.layout != "fixed_current_v1" or resolution.target is None):
            reason = resolution.error_code
            if reason is None and resolution.binding != "matching":
                reason = "project_relocation_required"
            raise DiagnosticError(reason if reason in PROJECT_REASONS else "project_unavailable")
        return resolution.paths

    def _reader(self, repo, task, parent, wait_id, **kwargs):
        reader = self.basis_factory(repo, task, parent, wait_id, **kwargs)
        def current():
            if not self._policy_enabled():
                raise BasisError()
            return reader()
        return current

    def _finalizer(self, binding):
        if not self._policy_enabled():
            raise BasisError()
        paths = self._location(enabling=False)
        locator_path = paths.review_preparation_store(binding.parent_thread_id, binding.task_id, binding.target_generation)
        preparation = None
        if path_lexically_exists(locator_path):
            from task_governance_tool.review_preparation_repository import PreparationRepository
            _, preparation = PreparationRepository(locator_path).read()
            expected = {**preparation.basis, "parent_thread_id": preparation.basis["parent_session_id"]}
            if any(expected.get(key) != value for key, value in asdict(binding).items() if key != "wait_id"):
                raise BasisError()
            if preparation.route == "manual":
                return None  # Presence of an older intent cannot promote this route.
        intent = paths.review_finalization_store(binding.parent_thread_id,
                                                 binding.task_id, binding.target_generation)
        if not path_lexically_exists(intent):
            return None
        from task_governance_tool.review_finalization import Finalizer
        from task_governance_tool.session_identity import CallerIdentity
        finalizer = Finalizer(self.config.repo, binding.task_id, CallerIdentity(binding.parent_thread_id),
                              generation=binding.target_generation)
        basis = finalizer.journal.read().basis
        if preparation is not None:
            from task_governance_tool.review_finalization import require_preparation_intent
            from task_governance_tool.session_identity import CallerIdentity
            if preparation.finalization_store != intent.name:
                raise BasisError()
            require_preparation_intent(preparation, finalizer.journal.read_rounds()[0], preparation.basis,
                CallerIdentity(binding.parent_thread_id))
        if any(basis.get(key) != value for key, value in asdict(binding).items() if key != "wait_id"):
            raise BasisError()
        return finalizer

    def _continuation(self, binding, round_record, *, historical=False):
        """Validate an operational round against current native authority.

        Exceptional continuation only; ordinary initial waits add no core read.
        Repository code owns the snapshot and SQL, never this host composer.
        """
        from task_governance_tool.review_handoff_scope import resolve
        from task_governance_tool.review_continuation_handoff_repository import read_state
        from task_governance_tool.review_continuation import basis_digest as native_digest, selection_digest
        from task_governance_tool.session_identity import CallerIdentity
        state = read_state(resolve(self.config.repo, writing=False).target, binding.task_id,
            CallerIdentity(binding.parent_thread_id))
        current = next((row for row in state.records if row.continuation_id == round_record.continuation_id), None)
        if (current is None or current.state not in ("prepared", "registered")
            or current.preparation_id != round_record.preparation_id or current.sequence != round_record.continuation_sequence
            or native_digest(current.basis) != round_record.basis_digest or selection_digest(current.selected) != round_record.selection_digest):
            raise DiagnosticError("review_continuation_conflict")
        if historical:
            # Validate history only; never reuse an old judgment or effect.
            identity = ("project_id", "project_path_hash", "project_binding_generation", "task_id", "parent_session_id")
            fresh = {**state.basis, "parent_thread_id": state.basis["parent_session_id"]}
            if (current.basis["target_generation"] >= state.basis["target_generation"]
                or any(current.basis[key] != state.basis[key] for key in identity)
                or any(fresh.get(key) != value for key, value in asdict(binding).items() if key != "wait_id")):
                raise DiagnosticError("review_continuation_basis_changed")
        elif current.basis != state.basis:
            raise DiagnosticError("review_continuation_basis_changed")
        return current

    def _wait_round_current(self, binding, round_record):
        """Prove the saved fence before a new occurrence can create host effects.

        Called again under request admission, which continuation preparation
        also holds before publishing a fence. Reads never settle a transition.
        """
        from .continuation_transition_repository import TransitionRepository
        from .managed_wait import basis_digest
        paths = self._location(enabling=False)
        transition_path = paths.review_continuation_store(binding.parent_thread_id, binding.task_id, binding.target_generation)
        if not path_lexically_exists(transition_path):
            if round_record is not None:
                raise DiagnosticError("review_continuation_conflict")
            return
        transitions = TransitionRepository(transition_path).read()
        if not transitions:
            if round_record is not None:
                raise DiagnosticError("review_continuation_conflict")
            return
        transition = transitions[-1]
        if (transition.phase not in ("prepared", "published") or round_record is None
            or transition.wait_basis_digest != basis_digest(binding)
            or replace(transition, phase="prepared") != replace(round_record, phase="prepared")):
            raise DiagnosticError("review_continuation_conflict")

    def _wait_occurrence_current(self, binding, automation_id):
        """Fence obsolete workers using the explicit operational transition."""
        from .continuation_transition_repository import TransitionRepository
        from .request_repository import RequestRepository
        from .managed_wait import basis_digest
        paths = self._location(enabling=False)
        transition_path = paths.review_continuation_store(binding.parent_thread_id, binding.task_id, binding.target_generation)
        if not path_lexically_exists(transition_path):
            return  # Legacy/initial occurrence, without a continuation fence.
        transitions = TransitionRepository(transition_path).read()
        if not transitions:
            return
        transition = transitions[-1]
        if transition.phase not in ("prepared", "published"):
            raise DiagnosticError("review_continuation_conflict")
        _, attempt, rounds = RequestRepository(paths.review_wait_request_store(binding.parent_thread_id, binding.task_id)).read_admission()
        if (attempt is None or attempt.automation_id != automation_id or attempt.continuation_id != transition.continuation_id
            or attempt.continuation_sequence != transition.continuation_sequence or attempt.basis_digest != basis_digest(binding)
            or not rounds or rounds[-1].continuation_id != transition.continuation_id or rounds[-1].phase != "published"):
            raise DiagnosticError("review_continuation_conflict")
        return attempt

    def _session(self, path, task_id, automation_id, *, managed=False, prepare_reviewer_reader=None):
        if automation_id in self.sessions:
            session = self.sessions[automation_id]
            if session.config.task_id != task_id:
                raise DiagnosticError("binding_mismatch")
            return session
        if len(self.sessions) >= 64:
            raise DiagnosticError("session_limit")
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
                *args, finalization_factory=lambda controller: self._finalizer(controller.binding),
                dispatch_path=lambda parent: self._location(enabling=False).review_wait_dispatch_store(parent),
                observation_factory=lambda controller: self.managed.observe(task_id,
                    {"threadId": controller.binding.parent_thread_id}, self._location(enabling=False)),
                runtime_observer=self._runtime,
                occurrence_guard=lambda controller: self._wait_occurrence_current(controller.binding, automation_id), **kwargs)
        session = self.session_factory(config, host_factory=host_factory,
            basis_factory=self._reader, clock=self.clock, **options)
        self.sessions[automation_id] = session
        return session

    def _ensure_root(self, paths):
        if not path_lexically_exists(paths.review_wait_root):
            create_physical_directory_exclusive(paths.review_wait_root, root=paths.fixed_root)
        inspect_physical_directory(paths.review_wait_root, root=paths.fixed_root)

    def _runtime(self):
        from task_governance_tool.state_resolver import SCHEMA_VERSION
        try:
            if self.runtime_identity is not None:
                return self.runtime_identity.inspect(loaded_schema=SCHEMA_VERSION)
        except Exception:
            pass
        return {"version": 1, "code_id": None, "supported_schema": None,
                "deployed_supported_schema": None, "comparison": "unknown"}

    def handle(self, operation, arguments, metadata):
        diagnostic = CallDiagnostics()
        admitted = False
        try:
            result = self._handle(operation, arguments, metadata, diagnostic)
            admitted = diagnostic.stage != "executor_admission" and diagnostic.stage != "request"
        except Exception as exc:
            result = diagnostic.failure({"ok": False, "error": "review_wait_unavailable"}, error=exc)
            admitted = diagnostic.stage not in {"request", "executor_admission"}
        if result.get("ok") is not True and "diagnostic" not in result:
            result = diagnostic.failure(result)
        if operation == "inspect" and admitted and self.runtime_identity is not None:
            from task_governance_tool.state_resolver import SCHEMA_VERSION
            try:
                runtime = self.runtime_identity.inspect(loaded_schema=SCHEMA_VERSION)
            except Exception:
                # Diagnosis never turns a readable wait into a failed operation.
                runtime = {"version": 1, "code_id": None, "supported_schema": None,
                           "deployed_supported_schema": None, "comparison": "unknown"}
            result = {**result, "runtime": runtime}
        if operation in {"inspect", "stop"} and admitted:
            from .worker_observation import observation
            from .review_wait_direct_repository import instant
            from datetime import datetime, timezone
            try:
                observed = result.get("observation")
                if observed is None:
                    if result.get("ok") is True:
                        paths = self._location(enabling=False)
                        observed = self.managed.observe(arguments["task_id"], metadata, paths)
                    else:
                        # The failed operation already observed its boundary;
                        # never repeat an unreadable-state read for reporting.
                        observed = observation(arguments["task_id"], metadata["threadId"],
                            instant(datetime.now(timezone.utc)), reason="observation_unavailable")
            except Exception:
                observed = observation(arguments["task_id"], metadata["threadId"],
                    instant((self.clock or (lambda: datetime.now(timezone.utc)))()),
                    reason="observation_unavailable")
            observed["runtime"] = result["runtime"] if "runtime" in result else self._runtime()
            result["observation"] = observed
            if operation == "stop":
                result["operation"] = {"status": "succeeded" if result.get("ok") is True else "unresolved",
                                       "cleanup": "not_needed" if result.get("status") == "absent" else
                                       "confirmed" if result.get("ok") is True else "unresolved"}
        return result

    def _handle(self, operation, arguments, metadata, diagnostic):
        try:
            if self.closed:
                return {"ok": False, "error": "service_closed"}
            if (operation not in OPERATIONS or type(arguments) is not dict
                    or set(arguments) != set(OPERATIONS[operation])):
                return {"ok": False, "error": "invalid_request"}
            diagnostic.stage = "executor_admission"
            actual = admit_executor_metadata(metadata)
            enabling = operation in {"wait", "prepare", "direct_delete_start"}
            if enabling:
                executor_turn_id(actual)
                diagnostic.stage = "policy"
                try:
                    enabled = self._policy_enabled()
                except DiagnosticError as exc:
                    diagnostic.capture(error=exc)
                    return {"ok": False, "error": "review_wait_not_enabled"}
                if not enabled:
                    return {"ok": False, "error": "review_wait_not_enabled"}
            diagnostic.stage = "project_admission"
            paths = self._location(enabling=enabling)
            if operation in {"wait", "inspect", "stop"}:
                diagnostic.stage = "request"
                validate_task_id(arguments["task_id"])
                return self.managed.handle(operation, arguments, actual, paths, diagnostic)
            automation = arguments["automation_id"]
            path = paths.review_wait_store(automation)
            if operation == "prepare":
                diagnostic.stage = "request"
                task_id = validate_task_id(arguments["task_id"])
                # Validate live ownership before creating even an empty directory.
                diagnostic.stage = "basis_before"
                self._reader(self.config.repo, task_id, actual["threadId"], "prepare",
                             helper=self.skill / "scripts/review_handoff.py")()
                diagnostic.stage = "state_create"
                diagnostic.local_state = "may_have_changed"
                self._ensure_root(paths)
                session = self._session(path, task_id, automation)
                args = {"reviewer_ids": arguments["reviewer_ids"]}
            else:
                diagnostic.stage = "request_read"
                controller = ReviewWaitRepository.open_existing(path).read().controller
                diagnostic.retained_effects = "present_unresolved"
                if (controller.binding.parent_thread_id != actual["threadId"]
                        or controller.reservation.timer_id != automation):
                    return {"ok": False, "error": "caller_mismatch"}
                session = self._session(path, controller.binding.task_id, automation)
                args = {key: value for key, value in arguments.items() if key != "automation_id"}
            diagnostic.stage = "operation"
            if operation not in {"view", "direct_status"}:
                diagnostic.local_state = "may_have_changed"
            if operation in {"direct_delete_start", "direct_cancel"}:
                diagnostic.host_mutation = "may_have_occurred"
            observed_effects = diagnostic.retained_effects
            diagnostic.invalidate_record()
            result = session.handle(operation, args, metadata)
            diagnostic.observe_operation(result, observed_effects)
            if result.get("ok") is not True:
                diagnostic.capture(result=result)
            return {**result, "automation_id": automation}
        except Exception:
            raise

    def close(self):
        self.closed = True
        succeeded = True
        for session in self.sessions.values():
            try:
                succeeded = session.close() == {"ok": True} and succeeded
            except Exception:
                succeeded = False
        return {"ok": True} if succeeded else {"ok": False, "error": "observer_cleanup_unknown"}


def main(argv=None, *, runtime_identity=None):
    parser = protocol._ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--server", required=True)
    parser.add_argument("--codex-home", required=True)
    parser.add_argument("--timezone", required=True)
    try:
        args = parser.parse_args(argv)
        config = ProjectConfig(Path(args.repo), Path(args.server), Path(args.codex_home), args.timezone)
        return serve(sys.stdin.buffer, sys.stdout.buffer, ProjectReviewWaitSession(config, runtime_identity=runtime_identity))
    except (Exception, KeyboardInterrupt):
        sys.stderr.write("Review-wait server unavailable.\n")
        return 1
