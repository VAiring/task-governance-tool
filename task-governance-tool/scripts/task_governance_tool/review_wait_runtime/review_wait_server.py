"""Source-only MCP entry point for one explicitly prepared review wait.

Discovery has no host effects. A genuine executor must supply metadata for
each control call. This server never installs itself, creates a reservation,
implicitly initializes a missing store or changes Hook trust. Its observer belongs to this
stdio session; the deletion variant also attempts bounded known-ACTIVE cleanup
on owning-session EOF.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import BinaryIO
from uuid import uuid4

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from task_governance_tool.review_wait_runtime import review_wait_mcp_relay as protocol


_INTEGER = {"type": "integer", "minimum": 0}
_OPERATIONS = {
    "prepare": {"reviewer_ids": {"type": "array", "minItems": 1, "maxItems": 64,
                                 "uniqueItems": True, "items": {"type": "string"}}},
    "view": {},
    "arm": {},
    "check": {"expected_arm": _INTEGER, "wake_id": {"type": "string"},
              "wake_time": {"type": "string"}},
    "rearm": {"expected_arm": _INTEGER, "healthy": {"type": "boolean"}},
    "cancel": {"expected_arm": _INTEGER},
    "direct_start": {},
    "direct_delete_start": {},
    "direct_status": {},
    "direct_cancel": {"probe_id": {"type": "string"}},
    "direct_ack": {"probe_id": {"type": "string"}},
}

_BOOTSTRAP_STAGES = frozenset({
    "request", "executor_admission", "host_context", "basis_before",
    "reviewer_resolution", "child_read", "heartbeat_read", "basis_after", "state_create", "service_load",
})
_BOOTSTRAP_REASONS = frozenset({
    "executor_context_required", "caller_mismatch", "invalid_request",
    "host_transport_unavailable", "invalid_host_configuration", "invalid_child_set",
    "invalid_child_id", "invalid_automation_id", "host_call_failed",
    "invalid_host_response", "child_status_unavailable", "timezone_not_admitted",
    "timezone_changed", "timezone_unavailable", "automation_config_unavailable",
    "automation_config_invalid", "automation_config_unsafe", "automation_config_changed",
    "wait_basis_invalid_arguments", "wait_basis_unavailable", "wait_basis_response_invalid",
    "state_path_invalid", "state_unreadable", "writer_busy", "revision_conflict",
    "invalid_snapshot", "unsupported_journal_mode", "state_transition_invalid",
    "invalid_configuration",
    "reviewer_identity_unavailable", "reviewer_identity_ambiguous",
})


def _bootstrap_failure(stage: str, error: Exception) -> dict:
    """Expose only fixed boundary diagnostics, never exception/provider data."""
    from task_governance_tool.review_wait_runtime.review_wait_basis import BasisError
    from task_governance_tool.review_wait_runtime.review_wait_host import HOST_BOUNDARY_REASONS, HostAdapterError
    from task_governance_tool.review_wait_runtime.review_wait_repository import RepositoryError
    from task_governance_tool.review_wait_runtime.review_wait_service import ServiceError
    from task_governance_tool.review_wait_runtime.failure_diagnostics import DiagnosticError
    reason = "candidate_unavailable"
    if type(error) is DiagnosticError:
        reason = error.reason
    if type(error) in (BasisError, HostAdapterError, RepositoryError, ServiceError):
        code = getattr(error, "code", None)
        if type(code) is str and code in _BOOTSTRAP_REASONS:
            reason = code
        if type(error) is HostAdapterError and code == "host_call_failed":
            detail = getattr(error, "boundary_reason", None)
            if type(detail) is str and detail in HOST_BOUNDARY_REASONS:
                reason = detail
    return {"ok": False, "error": "candidate_unavailable",
            "stage": stage if stage in _BOOTSTRAP_STAGES else "request", "reason": reason}


def catalogue() -> list[dict]:
    descriptions = {
        "prepare": "Explicitly initialize this candidate's scratch store for actual dispatched reviewers; no timer update.",
        "view": "Read the prepared development wait without starting a host operation.",
        "arm": "Arm the existing reservation for ten minutes and start its session-owned observer.",
        "check": "Stop and read back the matching scheduled check before handling review results.",
        "rearm": "Rearm a checked healthy unfinished review wait on its same reservation.",
        "cancel": "Close this review wait and confirm its same reservation is paused.",
        "direct_start": "Explicitly start one authorized same-parent message experiment with the timer paused.",
        "direct_delete_start": "Arm one ten-minute check; after actual reviews end, delete/confirm it and send once to the same idle parent.",
        "direct_status": "Inspect the direct-message experiment without starting or retrying a send.",
        "direct_cancel": "Stop waiting for the direct-message experiment; a dispatched message cannot be recalled.",
        "direct_ack": "Acknowledge this experiment in a new genuine turn of the same parent.",
    }
    return [{"name": "review_wait_" + name, "description": descriptions[name],
             "inputSchema": {"type": "object", "properties": fields,
                             "required": list(fields), "additionalProperties": False},
             "annotations": {"readOnlyHint": name in {"view", "direct_status"}}}
            for name, fields in _OPERATIONS.items()]


def _call(params: object, service) -> dict:
    operations = getattr(service, "operations", _OPERATIONS)
    if (not isinstance(params, dict) or set(params) != {"name", "arguments", "_meta"}
            or not isinstance(params["name"], str)
            or not params["name"].startswith("review_wait_")
            or params["name"][12:] not in operations
            or not isinstance(params["arguments"], dict)
            or not isinstance(params["_meta"], dict)):
        raise protocol.RelayError()
    operation = params["name"][12:]
    if set(params["arguments"]) != set(operations[operation]):
        raise protocol.RelayError()
    result = service.handle(operation, params["arguments"], params["_meta"])
    # Only this internal service's closed summary is serialized, never raw host
    # results, caller metadata, repository snapshots or exception details.
    return {"content": [{"type": "text", "text": protocol._encode(result).decode().rstrip("\n")}],
            "isError": result.get("ok") is not True}


def serve(stdin: BinaryIO, stdout: BinaryIO, service) -> int:
    state, exit_code = "new", 0

    def reply(request_id: object, *, result: object = None, code: int | None = None) -> None:
        message = {"jsonrpc": "2.0", "id": request_id}
        if code is None:
            message["result"] = result
        else:
            message["error"] = {"code": code, "message": "Invalid review-wait request."}
        stdout.write(protocol._encode(message))
        stdout.flush()

    try:
        while True:
            line = stdin.readline(protocol.MAX_MESSAGE_BYTES + 1)
            if not line:
                break
            try:
                if len(line) > protocol.MAX_MESSAGE_BYTES or not line.endswith(b"\n"):
                    raise protocol.RelayError()
                request = protocol.strict_json_loads(line)
            except protocol.RelayError:
                reply(None, code=-32700)
                exit_code = 1
                break
            if (not isinstance(request, dict) or request.get("jsonrpc") != "2.0"
                    or not isinstance(request.get("method"), str)
                    or set(request) - {"jsonrpc", "id", "method", "params"}):
                reply(None, code=-32600)
                continue
            method, params = request["method"], request.get("params", {})
            if "id" not in request:
                if (method == "notifications/initialized" and state == "initializing"
                        and protocol._valid_control_params(params)):
                    state = "ready"
                continue  # Notifications cannot launch an operation.
            request_id = request["id"]
            if type(request_id) not in (int, str):
                reply(None, code=-32600)
                continue
            if method == "initialize":
                if state != "new":
                    reply(request_id, code=-32600)
                elif (not isinstance(params, dict)
                      or not isinstance(params.get("protocolVersion"), str)
                      or not isinstance(params.get("capabilities"), dict)
                      or not protocol._valid_implementation(params.get("clientInfo"))):
                    reply(request_id, code=-32602)
                else:
                    version = params["protocolVersion"]
                    reply(request_id, result={
                        "protocolVersion": version if version in protocol.SUPPORTED_PROTOCOL_VERSIONS
                        else protocol.PROTOCOL_VERSION,
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "taskgov-review-wait", "version": "0.1.0"}})
                    state = "initializing"
            elif state != "ready":
                reply(request_id, code=-32600)
            elif method in {"ping", "tools/list"}:
                if not protocol._valid_control_params(params, paginated=method == "tools/list"):
                    reply(request_id, code=-32602)
                else:
                    reply(request_id, result={} if method == "ping" else
                          {"tools": getattr(service, "catalogue", catalogue)()})
            elif method == "tools/call":
                try:
                    result = _call(params, service)
                except protocol.RelayError:
                    reply(request_id, code=-32602)
                except Exception:
                    reply(request_id, code=-32603)
                else:
                    reply(request_id, result=result)
            else:
                reply(request_id, code=-32601)
    except (Exception, KeyboardInterrupt):
        exit_code = 1
    finally:
        try:
            if service.close() != {"ok": True}:
                exit_code = 1
        except Exception:
            exit_code = 1
    return exit_code


@dataclass(frozen=True)
class SessionConfig:
    service: object
    repo: Path
    task_id: str
    automation_id: str
    helper: Path | None = None


class ReviewWaitSession:
    """Development bootstrap; injected scratch state is never a Setup mode.

    The parent's explicit prepare call reads the current Task, admitted actual
    child turns and existing PAUSED reservation before creating one store. It
    cannot overwrite, migrate or recover a missing or corrupt former store.
    Source tests inject the same public boundaries; no test data is authority
    for a real Task or scheduler.
    """

    def __init__(self, config: SessionConfig, *, host_factory=None, basis_factory=None,
                 clock=None, service_factory=None, direct_factory=None, prepare_reviewer_reader=None):
        from task_governance_tool.review_wait_runtime.review_wait_host import PublicMcpHost
        from task_governance_tool.review_wait_runtime.review_wait_service import ReviewWaitService, ServiceConfig
        from task_governance_tool.review_wait_runtime.review_wait_basis import PublicTaskBasisReader
        if type(config) is not SessionConfig or type(config.service) is not ServiceConfig:
            raise ValueError("invalid_configuration")
        if not Path(config.repo).is_absolute() or not config.task_id or not config.automation_id:
            raise ValueError("invalid_configuration")
        self.config = config
        self.host_factory = host_factory or PublicMcpHost.from_environment
        self.basis_factory = basis_factory or PublicTaskBasisReader
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.service_factory = service_factory or ReviewWaitService
        self.direct_factory = direct_factory
        self.prepare_reviewer_reader = prepare_reviewer_reader
        self.service = None
        self.direct = None
        self.closed = False

    def _reader(self, parent, wait_id):
        kwargs = {} if self.config.helper is None else {"helper": self.config.helper}
        return self.basis_factory(self.config.repo, self.config.task_id, parent, wait_id, **kwargs)

    def _load(self):
        from task_governance_tool.review_wait_runtime.review_wait_repository import ReviewWaitRepository
        controller = ReviewWaitRepository.open_existing(self.config.service.repository_path).read().controller
        if (controller.binding.task_id != self.config.task_id
                or controller.reservation.timer_id != self.config.automation_id):
            raise ValueError("configuration_mismatch")
        reader = self._reader(controller.binding.parent_thread_id, controller.binding.wait_id)
        self.service = self.service_factory(self.config.service, reader, self.clock,
                                            host_factory=self.host_factory)
        self._basis_reader = reader

    def _direct(self):
        if self.direct is None:
            from task_governance_tool.review_wait_runtime.review_wait_direct import DirectProbe
            from task_governance_tool.review_wait_runtime.review_wait_repository import ReviewWaitRepository
            config = self.config.service
            controller = ReviewWaitRepository.open_existing(config.repository_path).read().controller
            reviewers = tuple(r.reviewer_id for r in controller.reviewers)
            def host(metadata):
                return self.host_factory(metadata=metadata, server_path=config.server_path,
                    automation_id=self.config.automation_id, codex_home=config.codex_home,
                    confirmed_timezone=config.confirmed_timezone,
                    timezone_source=config.timezone_source, reviewer_ids=reviewers)
            self.direct = (self.direct_factory or DirectProbe)(config.repository_path,
                self._basis_reader, host, self.clock)
        return self.direct

    def handle(self, operation, arguments, metadata):
        from task_governance_tool.review_wait_runtime.review_wait_controller import ReviewWaitController, Reviewer
        from task_governance_tool.review_wait_runtime.review_wait_repository import ReviewWaitRepository
        from task_governance_tool.review_wait_runtime.review_wait_runtime import reservation
        from task_governance_tool.review_wait_runtime.review_wait_service import _bounded_json, _uuid
        from task_governance_tool.review_wait_runtime.review_wait_host import reviewer_references
        stage = "request"
        try:
            if self.closed:
                return {"ok": False, "error": "service_closed"}
            if operation != "prepare":
                if self.service is None:
                    stage = "service_load"
                    self._load()
                if operation.startswith("direct_"):
                    return self._direct().handle(operation, arguments, metadata)
                if operation in {"arm", "rearm"} and self._direct().blocks_timer():
                    return {"ok": False, "error": "direct_probe_selected"}
                return self.service.handle(operation, arguments, metadata)
            if self.service is not None or self.config.service.repository_path.exists():
                return {"ok": False, "error": "state_already_exists"}
            if type(arguments) is not dict or set(arguments) != {"reviewer_ids"} or type(metadata) is not dict:
                return {"ok": False, "error": "invalid_request"}
            stage = "executor_admission"
            _bounded_json(metadata, 16_384, "executor_context_required")
            parent = _uuid(metadata.get("threadId"), "executor_context_required")
            children = arguments["reviewer_ids"]
            if type(children) is not list or not 1 <= len(children) <= 64:
                return {"ok": False, "error": "invalid_request"}
            children = reviewer_references(children, invalid_id_code="invalid_request")
            if len(set(children)) != len(children) or parent in children:
                return {"ok": False, "error": "invalid_request"}
            config = self.config.service
            stage = "host_context"
            host = self.host_factory(metadata=metadata, server_path=config.server_path,
                automation_id=self.config.automation_id, codex_home=config.codex_home,
                confirmed_timezone=config.confirmed_timezone, timezone_source=config.timezone_source,
                reviewer_ids=children)
            wait_id = "wait-" + uuid4().hex
            stage = "basis_before"
            reader = self._reader(parent, wait_id)
            binding = reader()
            if binding.parent_thread_id != parent or binding.task_id != self.config.task_id:
                return {"ok": False, "error": "task_binding_changed"}
            if any(child.startswith("/") for child in children):
                stage = "reviewer_resolution"
                children = host.resolve_reviewer_ids()
                children = tuple(_uuid(child, "invalid_request") for child in children)
                if len(children) != len(arguments["reviewer_ids"]) or len(set(children)) != len(children) or parent in children:
                    return {"ok": False, "error": "invalid_request"}
            reviewers = []
            stage = "child_read"
            for child in children:
                turn = (host.read_child(child) if self.prepare_reviewer_reader is None else
                        self.prepare_reviewer_reader(binding, host, child))
                if turn.child_id != child:
                    return {"ok": False, "error": "reviewer_turn_changed"}
                reviewers.append(Reviewer(child, _uuid(turn.turn_id, "invalid_request")))
            stage = "heartbeat_read"
            observed = host.view_heartbeat()
            if (observed.status != "PAUSED" or observed.timezone != config.confirmed_timezone
                    or observed.id != self.config.automation_id or observed.parent_thread_id != parent):
                return {"ok": False, "error": "heartbeat_not_admitted"}
            stage = "basis_after"
            if reader() != binding:
                return {"ok": False, "error": "task_binding_changed"}
            stage = "state_create"
            ReviewWaitRepository.create(config.repository_path,
                ReviewWaitController(binding, tuple(reviewers), reservation(observed)))
            stage = "service_load"
            self._load()
            return self.service.handle("view", {}, metadata)
        except Exception as exc:
            # Preparation never dispatched a timer mutation. Existing state is
            # inspected explicitly after any unknown/local publication failure.
            return _bootstrap_failure(stage, exc)

    def close(self):
        self.closed = True
        results = []
        for component in (self.direct, self.service):
            try:
                results.append({"ok": True} if component is None else component.close())
            except Exception:
                results.append({"ok": False})
        return ({"ok": True} if all(result == {"ok": True} for result in results)
                else {"ok": False, "error": "observer_cleanup_unknown"})


def main(argv=None) -> int:
    parser = protocol._ArgumentParser(description=__doc__)
    parser.add_argument("--development-store", required=True,
                        help="Absolute candidate scratch file; parent directory must already exist")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--automation-id", required=True)
    parser.add_argument("--server", required=True)
    parser.add_argument("--codex-home", required=True)
    parser.add_argument("--timezone", required=True,
                        help="Expected host timezone; fresh inherited Node Intl observations must agree")
    parser.add_argument("--helper", help="Explicit admitted source self-host review_handoff.py; no fallback search")
    try:
        args = parser.parse_args(argv)
        from task_governance_tool.review_wait_runtime.review_wait_service import ServiceConfig
        config = SessionConfig(ServiceConfig(Path(args.development_store), Path(args.server),
            Path(args.codex_home), args.timezone, "host_node_intl"), Path(args.repo),
            args.task_id, args.automation_id, None if args.helper is None else Path(args.helper))
        return serve(sys.stdin.buffer, sys.stdout.buffer, ReviewWaitSession(config))
    except (Exception, KeyboardInterrupt):
        sys.stderr.write("Review-wait candidate server unavailable.\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
