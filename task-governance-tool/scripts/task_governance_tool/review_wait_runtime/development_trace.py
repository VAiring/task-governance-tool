"""Opt-in source-checkout tracing. Never receives arguments or provider bodies.

One bounded queue and one exclusively created file per server process; no
rotation/deletion, host calls, retries, or Task/Evidence writes. Missing events
are not evidence of success or termination.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import threading
import time
from uuid import uuid4

DIRECTORY = ".tmp-review-wait-trace"
OPT_IN = b"taskgov-review-wait-trace-v1\n"
MAX_FILES = 32
MAX_BYTES = 2 * 1024 * 1024
MAX_RECORD_BYTES = 2048
QUEUE_SIZE = 128
_sink = None
_context = ContextVar("review_wait_development_trace", default={})
_finalization = ContextVar("review_wait_finalization_trace", default=None)
_collection = ContextVar("review_answer_collection_trace", default=None)
_events = frozenset({"server_started", "server_closing", "exchange_started", "exchange_finished",
    "read_finished", "worker_entered", "worker_returned", "worker_failure",
    "worker_joined", "timer_pause_started", "timer_pause_finished", "cleanup_failed",
    "runtime_observed", "finalization_checked", "collection_started", "collection_finished"})
_values = {
    "collection_origin": {"hook", "parent", "wait", "worker", "unknown"},
    "collection_kind": {"operation", "slot"},
    "collection_stage": {"entry", "admission", "source_lookup", "slot_lookup", "basis_check",
        "host_read", "page_validate", "turn_select", "content_check", "terminal_check",
        "final_select", "answer_decode", "observe", "publication", "readback", "returned"},
    "collection_status": {"collected", "unavailable", "partial", "disabled", "armed", "not_applicable"},
    "source_version": {"v1", "v2"},
    "read_kind": {"binding", "exact_answer", "terminal", "tracking_baseline", "tracking_answer"},
    "turn_error": {"present", "absent"},
    "content_scope": {"envelope", "thread", "turn", "answer", "dispatch"},
    "completeness": {"complete", "incomplete"},
    "final_count": {"none", "one", "multiple"},
    "tracking_state": {"watching", "frozen", "invalidated", "legacy"},
    "publication_state": {"none", "intent", "saved", "unknown"},
    "observation_state": {"missing", "unavailable", "complete", "conflict"},
    "saved_terminal": {"unknown", "in_progress", "completed", "failed", "interrupted"},
    "terminal_requirement": {"required", "not_required"},
    "decision": {"turn_not_completed", "turn_error", "content_incomplete", "boundary_excluded",
        "final_missing", "final_ambiguous", "observation_returned", "readback_incomplete"},
    "collection_failure_class": {"collection_unavailable", "binding_mismatch", "format_invalid",
        "capacity_exceeded", "privacy_rejected", "save_unknown", "registration_unknown"},
    "collection_failure_reason": {"incomplete", "missing", "shape", "identity", "conflict", "bound",
        "transport_unavailable", "effect_unknown", "unicode", "type", "value", "privacy"},
    "purpose": {"parent_state", "reviewer_state", "reviewer_identity", "notification_receipt"},
    "operation": {"read_thread", "wait_threads", "automation_update", "send_message_to_thread"},
    "stage": {"launch", "initialize", "response"},
    "outcome": {"returned", "failed", "confirmed", "unresolved"},
    "termination": {"not_attempted", "confirmed", "unresolved"},
    "turn_status": {"completed", "failed", "interrupted", "inProgress"},
    "thread_status": {"notLoaded", "idle", "active", "systemError"},
    "comparison": {"matching", "different", "unknown"},
    "finalization_stage": {"entry", "policy", "project_admission", "preparation_path",
        "preparation_read", "preparation_basis", "preparation_route", "intent_path",
        "finalizer_import", "finalizer_create", "intent_read", "preparation_store",
        "preparation_intent_read", "preparation_intent_match", "intent_basis", "reviewer_set"},
    "finalization_route": {"manual", "integrated", "absent"},
    "mismatch_field": {"project_id", "project_path_hash", "project_binding_generation",
        "task_id", "execution_id", "ownership_generation", "parent_thread_id",
        "contract_revision", "target_kind", "target_value", "target_base_revision",
        "target_generation", "artifact_manifest_id", "finalization_store"},
    "exception_kind": {"BasisError", "RepositoryError", "ReviewHandoffScopeError",
        "FinalizationError", "DiagnosticError", "AttributeError", "KeyError", "TypeError",
        "ImportError", "ModuleNotFoundError", "NameError", "OSError", "PermissionError",
        "ValueError", "RuntimeError", "KeyboardInterrupt", "SystemExit", "AnswerError", "other"},
}
_uuid = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")


def _fields(values):
    # Deny by default, including unrecognized keywords. Do not stringify objects.
    from .review_wait_host import HOST_BOUNDARY_REASONS
    from .review_wait_direct_repository import DIRECT_REASONS
    result = {}
    for key, value in values.items():
        if type(value) is not str:
            continue
        if key in _values and value in _values[key]:
            result[key] = value
        elif key == "reason" and value in HOST_BOUNDARY_REASONS | DIRECT_REASONS:
            result[key] = value
        elif key in {"probe_id", "worker_id", "parent_id", "turn_id", "exchange_id", "reviewer_id",
                     "collection_id", "parent_collection_id"} and _uuid.fullmatch(value):
            result[key] = value
        elif key == "automation_id" and re.fullmatch(r"taskgov-review-wait-[0-9a-f]{32}", value):
            result[key] = value
        elif key == "task_id" and re.fullmatch(r"tg_task_[0-9a-f]{16}", value):
            result[key] = value
        elif key == "code_id" and re.fullmatch(r"sha256:[0-9a-f]{64}", value):
            result[key] = value
        elif key == "failure_code":
            from .failure_diagnostics import REASONS
            if value in REASONS | {"finalization_project_unavailable", "review_continuation_basis_changed"}:
                result[key] = value
    return result


class _Trace:
    def __init__(self, stream):
        self.stream = stream
        self.queue = queue.Queue(QUEUE_SIZE)
        self.closed = threading.Event()
        self.instance = str(uuid4())
        self.sequence = 0
        self.dropped = 0
        self.lock = threading.Lock()
        self.worker = threading.Thread(target=self._write, name="review-wait-dev-trace", daemon=True)
        self.worker.start()

    def emit(self, event, values):
        if self.closed.is_set() or event not in _events:
            return
        # Never wait on a filesystem operation or another producer.
        if not self.lock.acquire(blocking=False):
            return
        try:
            self.sequence += 1
            record = {"version": 1, "instance": self.instance, "sequence": self.sequence,
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "monotonic_ns": time.monotonic_ns(), "event": event,
                "queue_dropped": self.dropped, **_fields(values)}
            data = (json.dumps(record, separators=(",", ":")) + "\n").encode("utf-8")
            if len(data) <= MAX_RECORD_BYTES:
                try:
                    self.queue.put_nowait(data)
                except queue.Full:
                    self.dropped += 1
        finally:
            self.lock.release()

    def _write(self):
        size = 0
        try:
            while not self.closed.is_set() or not self.queue.empty():
                try:
                    data = self.queue.get(timeout=0.1)
                except queue.Empty:
                    continue
                if size + len(data) > MAX_BYTES:
                    break
                self.stream.write(data)
                self.stream.flush()
                size += len(data)
        except Exception:
            pass
        finally:
            self.closed.set()
            try:
                self.stream.close()
            except Exception:
                pass

    def close(self):
        self.closed.set()
        self.worker.join(0.25)


def start(repo, entry):
    """Only a recognized physical source copy and explicit local marker opt in."""
    global _sink
    if _sink is not None:
        return
    stream = None
    try:
        from task_governance_tool.project_scope import inspect_project_scope
        from task_governance_tool.state_paths import inspect_physical_directory, read_physical_file_bounded
        from task_governance_tool.completion import safe_git_command, safe_git_environment
        repo, entry = Path(repo), Path(entry)
        directory = repo / DIRECTORY
        marker = directory / "enabled"
        if not marker.exists():
            return
        inspection = inspect_project_scope(repo=repo, repo_explicit=True, script_path=entry,
            include_runtime=False, include_package=False, include_ignore=False)
        if inspection.issues or inspection.scope is None or inspection.scope.layout != "source":
            return
        repo = inspection.scope.canonical_repo
        directory = repo / DIRECTORY
        marker = directory / "enabled"
        inspect_physical_directory(directory, root=repo)
        if read_physical_file_bounded(marker, root=repo, max_bytes=len(OPT_IN))[0] != OPT_IN:
            return
        # Trace output has its own explicit ignore admission, not state-ignore inference.
        checked = subprocess.run([*safe_git_command(repo), "check-ignore", "-q", "--", DIRECTORY + "/"],
            env=safe_git_environment(), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=2)
        if checked.returncode != 0:
            return
        for index in range(MAX_FILES):
            inspect_physical_directory(directory, root=repo)
            try:
                descriptor = os.open(directory / f"trace-{index:02d}.log",
                                     os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
                stream = os.fdopen(descriptor, "wb")
                break
            except FileExistsError:
                continue
        if stream is None:
            return
        _sink = _Trace(stream)
        emit("server_started")
    except Exception:
        if stream is not None:
            try:
                stream.close()
            except Exception:
                pass


def close():
    global _sink
    sink = _sink
    if sink is not None:
        emit("server_closing")
        _sink = None
        try:
            sink.close()
        except Exception:
            pass


def emit(event, **values):
    if _sink is not None:
        try:
            _sink.emit(event, {**_context.get(), **values})
        except Exception:
            pass


def exchange_id():
    return str(uuid4()) if _sink is not None else None


def runtime(observer):
    """One opt-in startup observation; never a hot-path source inventory."""
    if _sink is not None:
        try:
            value = observer()
            emit("runtime_observed", code_id=value.get("code_id"), comparison=value.get("comparison"))
        except Exception:
            emit("runtime_observed", comparison="unknown")


@contextmanager
def finalization_admission(task_id, parent_id):
    """One summary per explicit wait preflight, not per worker observation."""
    if _sink is None:
        yield
        return
    state = {"finalization_stage": "entry", "outcome": "returned"}
    token = _finalization.set(state)
    try:
        yield
    except BaseException as error:
        state["outcome"] = "failed"
        try:
            kind = type(error).__name__
            state["exception_kind"] = kind if kind in _values["exception_kind"] else "other"
            details = vars(error)
            state["failure_code"] = details.get("code", details.get("reason"))
        except Exception:
            pass  # Exception inspection cannot replace the original failure.
        raise
    finally:
        _finalization.reset(token)
        try:
            emit("finalization_checked", task_id=task_id, parent_id=parent_id,
                 exchange_id=exchange_id(), **state)
        except Exception:
            pass  # Correlation generation is diagnostic too, not an outcome gate.


def finalization_step(stage, *, mismatch_field=None, route=None):
    state = _finalization.get()
    if state is not None:
        state["finalization_stage"] = stage
        if mismatch_field is not None:
            state["mismatch_field"] = mismatch_field
        if route is not None:
            state["finalization_route"] = route


@contextmanager
def collection_scope(kind="operation", origin=None):
    """Correlate existing collection work; no answer bytes or extra observations."""
    if _sink is None:
        yield
        return
    try:
        parent = _context.get()
        identity = str(uuid4())
        fields = _fields({"collection_kind": kind, "collection_origin": origin or
            parent.get("collection_origin", "unknown"), "collection_id": identity,
            "parent_collection_id": parent.get("collection_id")})
        state = {"collection_stage": "entry", "outcome": "returned"}
    except Exception:
        yield
        return
    token = _collection.set(state)
    with context(**fields):
        emit("collection_started", **state)
        try:
            yield
        except BaseException as error:
            state["outcome"] = "failed"
            try:
                kind = type(error).__name__
                state["exception_kind"] = kind if kind in _values["exception_kind"] else "other"
                failure = vars(error).get("failure")
                if type(failure) is dict:
                    state.update(_fields({"collection_failure_class": failure.get("class"),
                        "collection_failure_reason": failure.get("reason")}))
            except Exception:
                pass
            raise
        finally:
            emit("collection_finished", **state)
            _collection.reset(token)


def collection_step(stage, **fields):
    """Retain only closed structural facts in the current operation/slot scope."""
    try:
        state = _collection.get()
        if state is not None:
            if stage == "host_read":
                for key in ("turn_id", "turn_status", "turn_error", "content_scope", "completeness",
                            "final_count", "decision"):
                    state.pop(key, None)
            state.update(_fields({"collection_stage": stage, **fields}))
    except Exception:
        pass


def collection_result(result):
    """Summarize bounded public return facts, without retaining result bodies."""
    try:
        state = _collection.get()
        if state is None:
            return
        if result is None:
            state["collection_status"] = "not_applicable"
        elif type(result) is dict:
            status = result.get("status")
            if status == "collection_unavailable":
                status = "unavailable"
            if "unavailable" in result:
                status = ("partial" if result.get("collected") else "unavailable") if result["unavailable"] else "collected"
            state.update(_fields({"collection_status": status}))
            if result.get("ok") is False:
                state["outcome"] = "failed"
    except Exception:
        pass


def collection_call(kind="operation", origin=None):
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            with collection_scope(kind, origin):
                result = function(*args, **kwargs)
                if kind == "operation":
                    collection_result(result)
                return result
        return wrapped
    return decorate


@contextmanager
def context(**values):
    token = _context.set({**_context.get(), **values}) if _sink is not None else None
    try:
        yield
    finally:
        if token is not None:
            _context.reset(token)


def purpose(name):
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            if _sink is None:
                return function(*args, **kwargs)
            with context(purpose=name):
                try:
                    result = function(*args, **kwargs)
                except Exception as error:
                    from .review_wait_host import host_failure
                    emit("read_finished", outcome="failed", **(host_failure(error) or {}))
                    raise
                # Only validated structural observations; never results, handles or receipt bodies.
                from .review_wait_host import ChildTurn
                fields = ({"turn_status": result.status, "thread_status": result.thread_status,
                           "turn_id": result.turn_id} if type(result) is ChildTurn else {})
                emit("read_finished", outcome="returned", **fields)
                return result
        return wrapped
    return decorate
