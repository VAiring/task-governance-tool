"""Project-scoped MCP controls for canonical, one-shot review waits.

Review discovery is inert. Explicit prepare owns only review-state creation;
Setup owns opt-in and the host owns configuration, trust and reservations.
The separately opted-in backup observer baselines old requests at startup;
no worker is recovered or retried from an old request.
"""

from dataclasses import asdict, dataclass, replace
from contextlib import nullcontext
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
from .review_wait_host import PublicMcpHost, HostAdapterError, admit_executor_metadata, executor_turn_id
from .review_wait_repository import ReviewWaitRepository
from .review_wait_server import ReviewWaitSession, SessionConfig, serve
from .review_wait_service import ServiceConfig
from .managed_host import ManagedHost
from .managed_direct import ManagedDirectProbe
from .managed_wait import ManagedWait
from .failure_diagnostics import CallDiagnostics, DiagnosticError, PROJECT_REASONS
from . import review_wait_mcp_relay as protocol


_ID = {"type": "string"}
_ANSWER_EVENT = {"type": "object", "properties": {
    "hook_event_name": {"type": "string", "const": "SubagentStop"}, "session_id": _ID,
    "agent_id": _ID, "turn_id": _ID, "stop_hook_active": {"type": "boolean"}},
    "required": ["hook_event_name", "session_id", "agent_id", "turn_id", "stop_hook_active"],
    "additionalProperties": False}
_ANSWER_REVIEWERS = {"type": "array", "minItems": 1, "maxItems": 8, "uniqueItems": True,
    "items": {"type": "string"}, "description": "Actual returned child UUIDs in immutable prepared-slot order."}
OPERATIONS = {
    "answer_collect": {"event": _ANSWER_EVENT, "task_id": _ID, "reviewer_ids": _ANSWER_REVIEWERS,
                       "action": {"type": "string", "const": "arm"}},
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
    tool_operations = {"review_answer_collect": "answer_collect"}
    argument_shapes = {"answer_collect": ({"event"}, {"task_id", "reviewer_ids"}, {"action", "task_id", "reviewer_ids"})}

    def __init__(self, config, *, host_factory=None, basis_factory=None,
                 session_factory=None, clock=None, managed_host_factory=None, runtime_identity=None,
                 answer_read_host_factory=None):
        if type(config) is not ProjectConfig or any(
                not path.is_absolute() for path in (config.repo, config.server_path, config.codex_home)):
            raise ValueError("invalid_configuration")
        self.config = config
        self.skill = Path(__file__).absolute().parents[3]
        self.host_factory = host_factory or ProjectHost.from_environment
        self.managed_host_factory = managed_host_factory or ManagedHost.from_environment
        from task_governance_tool.review_answer_read_host import AnswerReadHost
        self.answer_read_host_factory = answer_read_host_factory or AnswerReadHost.from_environment
        self.basis_factory = basis_factory or PublicTaskBasisReader
        self.session_factory = session_factory or ReviewWaitSession
        self.clock = clock
        self.sessions = {}
        self.closed = False
        self.managed = ManagedWait(self)
        self.runtime_identity = runtime_identity
        self.backup_observer = None

    def start_backup_observer(self):
        """Called once by the configured service entry, never by an MCP tool.

        Startup performs bounded control admission, then the independent
        observer checks only its opportunity file. Review policy and individual
        review-session lifetimes do not own this service responsibility.
        """
        if self.closed or self.backup_observer is not None or self.runtime_identity is None:
            return False
        from task_governance_tool.async_backup import launch_backup
        from task_governance_tool.async_backup_opportunity import current_compatibility
        from task_governance_tool.async_backup_repository import read_control, read_service_target
        from task_governance_tool.async_backup_service import BackupServiceObserver
        from task_governance_tool.debug_log import allow_output
        from task_governance_tool.storage import SCHEMA_VERSION

        def compatible():
            return self.runtime_identity.inspect(loaded_schema=SCHEMA_VERSION).get("comparison") == "matching"

        def launch(target):
            with self._worker_scope(operation="maintenance"):
                return launch_backup(target, _admit_launch=compatible)

        def admit_owner(target):
            with self._worker_scope(operation="maintenance"):
                control = read_control(target)
                allow_output()
                return control.maintenance.enabled

        try:
            with self._worker_scope(operation="maintenance"):
                inspection = inspect_project_scope(repo=self.config.repo, repo_explicit=True,
                    script_path=self.skill / "scripts/review_wait_server.py",
                    include_runtime=False, include_package=False, include_ignore=True)
                if (inspection.first_issue(allowed_codes=STRUCTURAL_CODES | {"state_ignore_required"})
                        or inspection.scope is None):
                    return False
                target = read_service_target(inspection.scope.skill_root,
                                             repo=inspection.scope.canonical_repo)
                if target is None:
                    return False
                allow_output()
                observer = BackupServiceObserver(target, compatibility=current_compatibility(),
                    compatible=compatible, launch=launch, admit_owner=admit_owner)
                self.backup_observer = observer
                return observer.start()
        except Exception:
            # No synchronous fallback, service restart or change to review tools.
            return False

    @staticmethod
    def catalogue():
        descriptions = {
            "answer_collect": "Explicitly enabled answer collection, independent of waiting. Hook arm accepts typed event identity hints only. Parent arm accepts the original Task and ordered actual child UUIDs. New tracking requests bind a fresh dedicated child once, then observe its latest complete final until the first valid completed answer is frozen. Invalid format remains unadopted; valid negative/unable results also freeze. For a newly prepared continuation, call with action=arm BEFORE sending its follow-up: the existing dedicated reviewer must have ended its prior turn, which becomes the excluded baseline. Repeating arm reuses that baseline, never resends. Old issued requests retain exact-turn recovery. Only the parent arm may reconcile an interrupted publication against its saved intent, exact path and full bytes. Returns bounded facts without answer bodies. No correction sends, workers, timers, registration or completion. Unknown sends never authorize replay or prove delivery; use the existing parent recovery.",
            "wait": "Wait for the original Task and actual returned reviewers using the authorized connected service. Creates and starts its same-parent timer; delegate waiting and end the turn on ok=true,status=waiting,parent_may_end=true. On failure, report the limitation and recovery condition before ordinary turn end; no automatic resumption is promised. A matching prepare-finalization intent runs fixed registration, gates, local commit and completion before notification; otherwise it notifies review end only. A received body matching the original Task/target generation with status=completed, successful stages, actual Task done and sufficient reporting facts without unresolved contradiction or action permits direct reporting without another guide read, routine query or replay. Include all Findings (also low/resolved), warnings, limitations and not_recorded work; these alone do not exclude success. Labels (including delivery.source_body=complete), correlation, ok=true, review end/PASS and send acceptance alone prove neither completion nor unseen content. Follow references/review_wait.md#normal-wait for waiting; other results, including incomplete, unknown, failed, conflicting or action-requiring reports, use references/task_workflow.md#continue-after-reviews. No separate ACK or routine status call; next-Task authority reads and existing gates still apply.",
            "inspect": "Read-only diagnosis of the caller's Task wait and runtime identity. A generated collected-review request may also ask its reviewer to call once for connection preparation in that reviewer's own context; absent is expected without an owned wait, and failure does not stop review. observation distinguishes request phase, exact worker running/stopped/unknown, direct versus saved joined termination, timer/send/receipt and observation time. No saved start flag proves life or exit; never starts/retries effects or writes evidence. Old notification observations are not current state. No routine parent poll or completion gate.",
            "stop": "Explicitly stop this parent's Task wait and clean up only known effects; no unknown-operation retry. operation reports cleanup separately from observation of the same worker running/stopped/unknown. Successful cleanup survives a later observation failure; unknown observation alone requires no repeat stop. Saved joined termination remains readable after restart.",
            "prepare": "Compatibility only: prepare the earlier per-reservation flow with an authorized PAUSED same-parent timer; no timer or send effect. Normal waiting uses review_wait_wait.",
            "view": "Compatibility/recovery only: inspect an earlier per-reservation wait without starting observation or host effects. Normal diagnosis uses review_wait_inspect.",
            "direct_delete_start": "Compatibility only: start an earlier prepared wait, delete its timer and send once to this same idle parent when its exact reviewers end. Requires send authorization. Normal waiting uses review_wait_wait.",
            "direct_status": "Compatibility/recovery only: inspect an earlier wait's timer, send and receipt; never retries. Normal diagnosis uses review_wait_inspect.",
            "direct_cancel": "Compatibility/recovery only: cancel an earlier wait and clean up known-ACTIVE state; never retry unknown effects. Normal cancellation uses review_wait_stop.",
            "direct_ack": "Compatibility only: acknowledge an earlier wait in a genuinely new turn of this same parent, without review evidence. Normal waiting records receipt automatically.",
        }
        return [{"name": "review_answer_collect" if operation == "answer_collect" else "review_wait_" + operation, "description": descriptions[operation],
                 "inputSchema": {"type": "object", "properties": fields,
                                 **({"oneOf": [{"type": "object", "properties": {key: fields[key] for key in shape},
                                     "required": sorted(shape), "additionalProperties": False}
                                     for shape in ProjectReviewWaitSession.argument_shapes[operation]]}
                                    if operation == "answer_collect" else {"required": list(fields)}),
                                 "additionalProperties": False},
                 "annotations": {"readOnlyHint": operation in {"view", "direct_status", "inspect"}}}
                for operation, fields in OPERATIONS.items()]

    def _enabled(self):
        return read_choices(self.skill)[1].get("review_wait") is True

    def _policy_enabled(self):
        try:
            return self._enabled()
        except Exception:
            raise DiagnosticError("policy_unreadable") from None

    def _collect_answers(self, binding, host, reviewers=None, *, explicit_recovery=False, diagnostic=None,
                         observation=None):
        from task_governance_tool.review_answer_host import PublicAnswerHost
        from task_governance_tool.review_answer_runtime import sources_for_reviewers, collect_sources
        if observation is not None and not observation.collection_allowed():
            return None
        with diagnostic.reading_retained() if diagnostic is not None else nullcontext():
            options = {} if observation is None else {"observe_policy": observation.observe_collection_policy}
            groups = sources_for_reviewers(self.config.repo, self.skill, binding, reviewers, **options)
        if groups is None:
            # OFF, no preparation and legacy are normal wait non-applicability.
            if observation is not None:
                observation.collection_disabled()
            return None
        adapter_factory = getattr(observation, "make_host", PublicAnswerHost)
        adapter = adapter_factory(host, binding.parent_thread_id, executor_turn_id(host._metadata))
        result = dict(collected=[], unavailable=[])
        for group in groups:
            with diagnostic.collecting(group.service.repository) if diagnostic is not None else nullcontext():
                observed = collect_sources(group, adapter, explicit_recovery=explicit_recovery,
                                           on_retryable=getattr(observation, "retry_unprocessed", None))
                result["collected"].extend(observed["collected"])
                result["unavailable"].extend(observed["unavailable"])
        return result

    def _bind_answers(self, binding, host, reviewers, *, fresh_context, diagnostic=None):
        from task_governance_tool.review_answer_host import PublicAnswerHost
        from task_governance_tool.review_answer_runtime import sources_for_reviewers, bind_sources, bound_turn
        with diagnostic.reading_retained() if diagnostic is not None else nullcontext():
            groups = sources_for_reviewers(self.config.repo, self.skill, binding, reviewers)
        if groups is None:
            return None
        adapter = PublicAnswerHost(host, binding.parent_thread_id, executor_turn_id(host._metadata))
        bound = {}
        for group in groups:
            with diagnostic.collecting(group.service.repository) if diagnostic is not None else nullcontext():
                records = bind_sources(group, adapter, creation_context=fresh_context)
                bound.update({row["binding"]["reviewer_session_id"]: bound_turn(row) for row in records})
        return bound

    def _answer_tracking(self, binding, reviewers):
        from task_governance_tool.review_answer_runtime import tracking_for_reviewers
        return tracking_for_reviewers(self.config.repo, self.skill, binding, reviewers)

    def _tracking_observer(self, binding, reviewers):
        from task_governance_tool.review_answer_runtime import tracking_observer_for_reviewers
        return tracking_observer_for_reviewers(self.config.repo, self.skill, binding, reviewers)

    def _control_observer(self, paths, binding):
        from task_governance_tool.review_wait_control_observation import ReviewWaitControlObservation
        return ReviewWaitControlObservation(paths.database, binding.project_id, binding.task_id)

    def _polling_sources(self, binding, automation_id, reviewers, *, collect=True):
        # Resolve once for this worker's effect-free operational observations.
        # Every later effect keeps its original resolver/basis admission.
        paths = self._location(enabling=False)
        # Saved native results can remain valid after completion releases the
        # active owner required by collection. Their wait still observes controls.
        tracking = self._tracking_observer(binding, reviewers) if collect else None
        control = self._control_observer(paths, binding)
        # The first control observation must reach the guard below: its new
        # baseline can include changes after the worker's initial admission.
        # The caller re-admits after True; later changes remain observable.
        candidate = None
        if hasattr(tracking, "candidate_changed"):
            def candidate(host, reviewer):
                from task_governance_tool.review_answer_host import PublicAnswerHost
                adapter = PublicAnswerHost(host, binding.parent_thread_id, executor_turn_id(host._metadata))
                return tracking.candidate_changed(adapter, reviewer)
        def guard():
            if not self._policy_enabled():
                raise BasisError()
            self._wait_occurrence_current(binding, automation_id, paths=paths, observation=True)
            return control()
        return guard, tracking, candidate

    def _collect_hook(self, event, metadata, diagnostic):
        from task_governance_tool.review_answer import AnswerError
        from task_governance_tool.review_answer_hook import decode_event, PENDING_RESULT
        from task_governance_tool.review_answer_host import PublicAnswerHost
        from task_governance_tool.review_answer_read_host import hook_executor_turn_id
        from task_governance_tool.review_answer_runtime import (
            service_for_event, collect_hook_event, reviewer_hook_admission)
        from task_governance_tool.setup_feature_config import answer_collection_allowed
        if not answer_collection_allowed(self.skill):
            return {"ok": True, "status": "disabled"}
        self._location(enabling=True)
        # Hook hints only locate a candidate. A reviewer executor also needs
        # its independently saved assignment and versioned source policy below.
        hint = decode_event(protocol._encode(event))
        reviewer_caller = hint["reviewer_session_id"] == metadata["threadId"]
        if hint["parent_session_id"] != metadata["threadId"] and not reviewer_caller:
            diagnostic.hook_caller_relation = "other"
            return {"ok": False, "error": "caller_mismatch"}
        try:
            caller_turn = hook_executor_turn_id(metadata)
        except HostAdapterError as error:
            diagnostic.hook_executor_context = error.executor_context
            raise
        service, binding = service_for_event(self.config.repo, self.skill,
            hint["parent_session_id"], hint["reviewer_session_id"])
        if binding.parent_thread_id != hint["parent_session_id"]:
            raise AnswerError("binding_mismatch", reason="identity")
        if reviewer_caller:
            source_admission = service.admit_basis
            def admit_current(value, slot, *, validation_reuse):
                with diagnostic.reading_retained():
                    source_admission.check_hook(value, slot, parent_id=binding.parent_thread_id,
                        validation_reuse=validation_reuse)
            try:
                with reviewer_hook_admission(service, binding.parent_thread_id,
                        metadata["threadId"], caller_turn, admit_current=admit_current) as admission:
                    host = self.answer_read_host_factory(metadata=metadata,
                        reviewer_id=metadata["threadId"], reviewer_turn_id=admission.turn_id, admit=admission.check,
                        server_path=self.config.server_path, automation_id="answer-hook",
                        codex_home=self.config.codex_home, confirmed_timezone=self.config.timezone,
                        timezone_source="host_node_intl")
                    adapter = PublicAnswerHost(host, binding.parent_thread_id)
                    with diagnostic.collecting(service.repository):
                        outcome = collect_hook_event(event, service=service, host=adapter)
            except HostAdapterError as error:
                if error.code != "caller_mismatch":
                    raise
                diagnostic.hook_caller_relation = "event_reviewer"
                return {"ok": False, "error": "caller_mismatch"}
            return {"ok": True, **PENDING_RESULT} if outcome == PENDING_RESULT else {"ok": True, "status": "collected"}
        host = self.managed_host_factory(metadata=metadata, task_id=binding.task_id,
            server_path=self.config.server_path, automation_id="answer-hook", codex_home=self.config.codex_home,
            confirmed_timezone=self.config.timezone, timezone_source="host_node_intl", reviewer_ids=(hint["reviewer_session_id"],))
        adapter = PublicAnswerHost(host, binding.parent_thread_id, caller_turn)
        with diagnostic.collecting(service.repository):
            outcome = collect_hook_event(event, service=service, host=adapter)
        return {"ok": True, **PENDING_RESULT} if outcome == PENDING_RESULT else {"ok": True, "status": "collected"}

    def _collect_parent(self, arguments, metadata, diagnostic):
        from task_governance_tool.review_answer import AnswerError
        from task_governance_tool.review_answer_host import _uuid as answer_uuid
        from task_governance_tool.setup_feature_config import answer_collection_allowed
        from .request_repository import RequestRepository
        from .managed_wait import basis_digest
        if not answer_collection_allowed(self.skill):
            return {"ok": False, "error": "collection_unavailable"}
        if "action" in arguments and arguments["action"] != "arm":
            raise AnswerError("collection_unavailable", reason="shape")
        reviewers = arguments["reviewer_ids"]
        if (type(reviewers) is not list or not 1 <= len(reviewers) <= 8
                or any(type(value) is not str for value in reviewers)
                or len(set(reviewers)) != len(reviewers)):
            raise AnswerError("binding_mismatch", reason="shape")
        for reviewer in reviewers:
            answer_uuid(reviewer)
        if metadata["threadId"] in reviewers:
            raise AnswerError("binding_mismatch", reason="identity")
        paths = self._location(enabling=True)
        task_id = validate_task_id(arguments["task_id"])
        # The public basis reader itself is independent of the wait switch.
        reader = self.basis_factory(self.config.repo, task_id, metadata["threadId"], "answer-parent",
                                   helper=self.skill / "scripts/review_handoff.py")
        binding = reader()
        if binding.parent_thread_id != metadata["threadId"] or binding.task_id != task_id:
            return {"ok": False, "error": "caller_mismatch"}
        host = self.managed_host_factory(metadata=metadata, task_id=task_id,
            server_path=self.config.server_path, automation_id="answer-parent", codex_home=self.config.codex_home,
            confirmed_timezone=self.config.timezone, timezone_source="host_node_intl", reviewer_ids=arguments["reviewer_ids"])
        if arguments.get("action") == "arm":
            from task_governance_tool.review_answer_host import PublicAnswerHost
            from task_governance_tool.review_answer_runtime import sources_for_reviewers, arm_sources
            groups = sources_for_reviewers(self.config.repo, self.skill, binding, reviewers)
            if groups is None:
                raise AnswerError("collection_unavailable", reason="identity")
            adapter = PublicAnswerHost(host, binding.parent_thread_id, executor_turn_id(metadata))
            for group in groups:
                with diagnostic.collecting(group.service.repository):
                    arm_sources(group, adapter, repo=self.config.repo, skill=self.skill, binding=binding)
            if reader() != binding:
                raise AnswerError("binding_mismatch", reason="identity")
            return dict(ok=True, status="armed", task_id=task_id, bound=True, reviewer_ids=reviewers)
        request_path = paths.review_wait_request_store(metadata["threadId"], task_id)
        previous = RequestRepository(request_path).read()[1] if path_lexically_exists(request_path) else None
        fresh = previous is None or previous.basis_digest != basis_digest(binding)
        # bind_initial validates the exact ordered UUID set and the current
        # same-parent started dispatch before any missing binding is admitted.
        bound = self._bind_answers(binding, host, arguments["reviewer_ids"], fresh_context=fresh, diagnostic=diagnostic)
        with diagnostic.reading_retained():
            if bound is None or reader() != binding:
                raise AnswerError("collection_unavailable", reason="identity")
        # This explicit parent collection alone may settle a publication intent by
        # exact physical readback. Hook/wait/restart never adopt unknown residue.
        result = self._collect_answers(binding, host, arguments["reviewer_ids"], explicit_recovery=True, diagnostic=diagnostic)
        if result is None:
            diagnostic.invalidate_record()
            raise AnswerError("collection_unavailable", reason="missing")
        return {"ok": not result["unavailable"], "status": "collected" if not result["unavailable"] else "collection_unavailable",
                "task_id": task_id, "bound": True, **result}

    def _location(self, *, enabling):
        inspection = inspect_project_scope(repo=self.config.repo, repo_explicit=True,
            script_path=self.skill / "scripts/review_wait_server.py",
            include_runtime=False, include_package=False, include_ignore=enabling)
        codes = STRUCTURAL_CODES | ({"state_ignore_required"} if enabling else set())
        issue = inspection.first_issue(allowed_codes=codes)
        if issue or inspection.scope is None:
            raise DiagnosticError(issue.code if issue and issue.code in PROJECT_REASONS else "project_unavailable")
        resolution = resolve_project_state(skill_root=inspection.scope.skill_root,
                                           repo=inspection.scope.canonical_repo, operation_local=True)
        if (resolution.error_code or resolution.binding != "matching"
                or resolution.layout != "fixed_current_v1" or resolution.target is None):
            reason = resolution.error_code
            if reason is None and resolution.binding != "matching":
                reason = "project_relocation_required"
            raise DiagnosticError(reason if reason in PROJECT_REASONS else "project_unavailable")
        from task_governance_tool.debug_log import allow_output
        allow_output()
        return resolution.paths

    def _reader(self, repo, task, parent, wait_id, **kwargs):
        reader = self.basis_factory(repo, task, parent, wait_id, **kwargs)
        def current():
            if not self._policy_enabled():
                raise BasisError()
            result = reader()
            from task_governance_tool.debug_log import allow_output
            allow_output()
            return result
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

    def _wait_occurrence_current(self, binding, automation_id, *, paths=None, observation=False):
        """Fence obsolete workers using the explicit operational transition.

        Only ordinary polling selects busy-deferable snapshots. Effect guards
        retain ordinary admission and the same complete occurrence validation.
        """
        from .continuation_transition_repository import TransitionRepository
        from .request_repository import RequestRepository
        from .managed_wait import basis_digest
        paths = self._location(enabling=False) if paths is None else paths
        transition_path = paths.review_continuation_store(binding.parent_thread_id, binding.task_id, binding.target_generation)
        if not path_lexically_exists(transition_path):
            return  # Legacy/initial occurrence, without a continuation fence.
        repository = TransitionRepository(transition_path)
        transitions = repository.read_observation() if observation else repository.read()
        if not transitions:
            return
        transition = transitions[-1]
        if transition.phase not in ("prepared", "published"):
            raise DiagnosticError("review_continuation_conflict")
        repository = RequestRepository(paths.review_wait_request_store(binding.parent_thread_id, binding.task_id))
        _, attempt, rounds = (repository.read_admission_observation() if observation else repository.read_admission())
        if (attempt is None or attempt.automation_id != automation_id or attempt.continuation_id != transition.continuation_id
            or attempt.continuation_sequence != transition.continuation_sequence or attempt.basis_digest != basis_digest(binding)
            or not rounds or rounds[-1].continuation_id != transition.continuation_id or rounds[-1].phase != "published"):
            raise DiagnosticError("review_continuation_conflict")
        return attempt

    def _session(self, path, task_id, automation_id, *, managed=False, prepare_reviewer_reader=None,
                 collection_reviewers=None):
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
        options = {"diagnostic_scope": self._worker_scope}
        host_factory = self.host_factory
        if managed:
            def answer_reviewers(controller):
                stored = tuple(reviewer.reviewer_id for reviewer in controller.reviewers)
                if collection_reviewers is None:
                    return stored  # Recovery can clean up, never restart this worker.
                if (len(collection_reviewers) != len(stored)
                        or set(collection_reviewers) != set(stored)):
                    raise DiagnosticError("invalid_child_set")
                # Controller identity is UUID-sorted; source admission retains
                # the independently admitted prepared-slot order for this worker.
                return collection_reviewers
            host_factory = lambda **kwargs: self.managed_host_factory(task_id=task_id, **kwargs)
            options["prepare_reviewer_reader"] = prepare_reviewer_reader
            options["direct_factory"] = lambda *args, **kwargs: ManagedDirectProbe(
                *args, finalization_factory=lambda controller: self._finalizer(controller.binding),
                dispatch_path=lambda parent: self._location(enabling=False).review_wait_dispatch_store(parent),
                observation_factory=lambda controller: self.managed.observe(task_id,
                    {"threadId": controller.binding.parent_thread_id}, self._location(enabling=False)),
                runtime_observer=self._runtime,
                answer_collector=lambda host, controller: self._collect_answers(controller.binding, host,
                    answer_reviewers(controller)),
                answer_tracking=lambda controller: self._answer_tracking(controller.binding,
                    answer_reviewers(controller)),
                tracked_answer_collector=lambda host, controller, reviewer, **options: self._collect_answers(
                    controller.binding, host, (reviewer,), **options),
                polling_factory=lambda controller, **options: self._polling_sources(controller.binding, automation_id,
                    answer_reviewers(controller), **options),
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

    def _worker_scope(self, *, operation="review_wait_worker"):
        from task_governance_tool.debug_log import operation_scope
        return operation_scope(self.config.repo, entrypoint=self.skill / "scripts/review_wait_server.py",
            operation=operation, repo_explicit=True, independent=True)

    def handle(self, operation, arguments, metadata):
        from task_governance_tool.debug_log import operation_scope
        with operation_scope(self.config.repo, entrypoint=self.skill / "scripts/review_wait_server.py",
                operation="review_wait", repo_explicit=True,
                read_only=operation in {"inspect", "view", "direct_status"}):
            return self._handle_scoped(operation, arguments, metadata)

    def _handle_scoped(self, operation, arguments, metadata):
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
        if getattr(diagnostic, "answer_collection", None) is not None:
            result = {**result, "answer_collection": diagnostic.answer_collection}
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
                    or set(arguments) not in self.argument_shapes.get(operation, (set(OPERATIONS[operation]),))):
                return {"ok": False, "error": "invalid_request"}
            diagnostic.stage = "executor_admission"
            actual = admit_executor_metadata(metadata)
            if operation == "answer_collect":
                diagnostic.stage = "operation"
                from task_governance_tool.review_answer import AnswerError
                try:
                    if "event" in arguments:
                        return self._collect_hook(arguments["event"], actual, diagnostic)
                    return self._collect_parent(arguments, actual, diagnostic)
                except AnswerError as error:
                    return {"ok": False, "error": error.failure["class"], "failure": error.failure}
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
        if self.backup_observer is not None:
            try:
                self.backup_observer.close()
            except Exception:
                pass
        succeeded = True
        for session in self.sessions.values():
            try:
                # A finite scope per existing session, including EOF cleanup.
                # Its existing exact owned-worker checks admit publication;
                # a read-only-only session has no such proof and emits nothing.
                with self._worker_scope(operation="review_wait_cleanup"):
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
        session = ProjectReviewWaitSession(config, runtime_identity=runtime_identity)
        session.start_backup_observer()
        return serve(sys.stdin.buffer, sys.stdout.buffer, session)
    except (Exception, KeyboardInterrupt):
        sys.stderr.write("Review-wait server unavailable.\n")
        return 1
