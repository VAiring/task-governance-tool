"""Development-only, read-only MCP relay for one review-wait reservation.

This is not a taskgov command or an installed Hook. The genuine MCP executor
must supply request metadata. Only the configured reservation's public ``view``
operation is forwarded; successful presentation text becomes valid Hook JSON.
No child response, diagnostic, environment value, or caller metadata is logged.
"""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
from typing import BinaryIO, Sequence


TOOL_NAME = "view_review_wait_reservation"
FAILURE_TEXT = "Review-wait reservation view failed."
PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_PROTOCOL_VERSIONS = ("2024-11-05", "2025-03-26", PROTOCOL_VERSION)
MAX_MESSAGE_BYTES = 262_144
MAX_QUEUED_MESSAGES = 16
MAX_DRAIN_BYTES = 1_048_576
CALL_TIMEOUT_SECONDS = 8.0
CLEANUP_TIMEOUT_SECONDS = 1.0
POLL_SECONDS = 0.01
RESPONSE_FAILURE_REASONS = frozenset({
    "response_timeout", "response_io_or_limit", "response_eof",
    "response_json_invalid", "response_envelope_invalid",
    "server_request_unsupported", "notification_unsupported",
    "rpc_parse_error", "rpc_invalid_request", "rpc_method_not_found",
    "rpc_invalid_params", "rpc_internal_error", "rpc_error",
})
_RPC_FAILURE_REASONS = {
    -32700: "rpc_parse_error", -32600: "rpc_invalid_request",
    -32601: "rpc_method_not_found", -32602: "rpc_invalid_params",
    -32603: "rpc_internal_error",
}


class RelayError(Exception):
    """A failure whose details must not cross the relay boundary."""

    def __init__(self, *args, response_reason: str | None = None):
        self.response_reason = (response_reason if type(response_reason) is str
                                and response_reason in RESPONSE_FAILURE_REASONS else None)
        super().__init__(*args)


def strict_json_loads(data: bytes | str) -> object:
    """Reject duplicate object keys, non-finite numbers and oversized records."""
    def pairs(items: list[tuple[str, object]]) -> dict:
        result: dict = {}
        for key, value in items:
            if key in result:
                raise RelayError()
            result[key] = value
        return result

    def number(value: str) -> float:
        parsed = float(value)
        if not math.isfinite(parsed):
            raise RelayError()
        return parsed

    def constant(_value: str) -> object:
        raise RelayError()

    try:
        if isinstance(data, bytes):
            if len(data) > MAX_MESSAGE_BYTES:
                raise RelayError()
            data = data.decode("utf-8", errors="strict")
        elif not isinstance(data, str) or len(data.encode("utf-8")) > MAX_MESSAGE_BYTES:
            raise RelayError()
        return json.loads(data, object_pairs_hook=pairs, parse_float=number,
                          parse_constant=constant)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise RelayError() from exc


def _encode(value: object) -> bytes:
    try:
        data = json.dumps(value, ensure_ascii=False, allow_nan=False,
                          separators=(",", ":")).encode("utf-8") + b"\n"
        if len(data) > MAX_MESSAGE_BYTES:
            raise RelayError()
        return data
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise RelayError() from exc


def validate_tool_call(params: object) -> dict:
    """Return the original metadata object; never derive or rewrite identity."""
    if not isinstance(params, dict) or set(params) != {"name", "arguments", "_meta"}:
        raise RelayError()
    if params["name"] != TOOL_NAME or params["arguments"] != {}:
        raise RelayError()
    if not isinstance(params["arguments"], dict):
        raise RelayError()
    metadata = params["_meta"]
    if not isinstance(metadata, dict):
        raise RelayError()
    thread_id = metadata.get("threadId")
    if not isinstance(thread_id, str) or not thread_id.strip():
        raise RelayError()
    # Also validate direct Python callers before process launch. A round trip
    # rejects non-JSON mapping keys; wire callers already passed strict parsing.
    if strict_json_loads(_encode(metadata)) != metadata:
        raise RelayError()
    return metadata


def _failure() -> dict:
    return {"content": [{"type": "text", "text": FAILURE_TEXT}], "isError": True}


def _valid_implementation(value: object) -> bool:
    return (isinstance(value, dict)
            and isinstance(value.get("name"), str) and bool(value["name"].strip())
            and isinstance(value.get("version"), str) and bool(value["version"].strip()))


class _ChildSession:
    """Own a single direct child and bounded, nonblocking pipe-draining workers."""

    def __init__(self, command: Sequence[str], cwd: Path | str | None,
                 deadline: float, cleanup_seconds: float):
        self.deadline = deadline
        self.cleanup_seconds = cleanup_seconds
        self.messages: queue.Queue[bytes | None] = queue.Queue(MAX_QUEUED_MESSAGES)
        self.failed = threading.Event()
        self.stop = threading.Event()
        self.workers: list[threading.Thread] = []
        self.process = subprocess.Popen(
            list(command), cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, bufsize=0, shell=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        try:
            for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
                os.set_blocking(stream.fileno(), False)
            for stream, capture in ((self.process.stdout, True), (self.process.stderr, False)):
                worker = threading.Thread(target=self._drain, args=(stream, capture),
                                          name="review-wait-relay-pipe")
                self.workers.append(worker)
                worker.start()
        except Exception:
            self.close()
            raise RelayError() from None

    def _publish(self, item: bytes | None) -> None:
        try:
            self.messages.put_nowait(item)
        except queue.Full:
            self.failed.set()

    def _drain(self, stream: BinaryIO, capture: bool) -> None:
        pending = bytearray()
        total = 0
        try:
            while not self.stop.is_set():
                try:
                    chunk = os.read(stream.fileno(), 8192)
                except BlockingIOError:
                    self.stop.wait(POLL_SECONDS)
                    continue
                if not chunk:
                    if capture:
                        if pending:
                            self.failed.set()
                        self._publish(None)
                    return
                total += len(chunk)
                if total > MAX_DRAIN_BYTES:
                    self.failed.set()
                    return
                if not capture:
                    continue  # Deliberately discard stderr, including on failure.
                pending.extend(chunk)
                while b"\n" in pending:
                    end = pending.index(b"\n") + 1
                    if end > MAX_MESSAGE_BYTES:
                        self.failed.set()
                        return
                    self._publish(bytes(pending[:end]))
                    del pending[:end]
                    if self.failed.is_set():
                        return
                if len(pending) >= MAX_MESSAGE_BYTES:
                    self.failed.set()
                    return
        except (OSError, ValueError):
            if not self.stop.is_set():
                self.failed.set()

    def send(self, data: bytes) -> None:
        offset = 0
        while offset < len(data):
            if self.failed.is_set() or time.monotonic() >= self.deadline:
                raise RelayError()
            try:
                count = os.write(self.process.stdin.fileno(), data[offset:])
            except BlockingIOError:
                self.stop.wait(POLL_SECONDS)
                continue
            except (OSError, ValueError) as exc:
                raise RelayError() from exc
            if count <= 0:
                raise RelayError()
            offset += count

    def result(self, request_id: int) -> dict:
        while True:
            remaining = self.deadline - time.monotonic()
            if self.failed.is_set():
                raise RelayError(response_reason="response_io_or_limit")
            if remaining <= 0:
                raise RelayError(response_reason="response_timeout")
            try:
                data = self.messages.get(timeout=min(POLL_SECONDS, remaining))
            except queue.Empty:
                continue
            if data is None:
                raise RelayError(response_reason="response_eof")
            try:
                message = strict_json_loads(data)
            except RelayError:
                raise RelayError(response_reason="response_json_invalid") from None
            # Any server-initiated request (including elicitation), notification,
            # wrong id, JSON-RPC error or extra envelope field fails closed.
            # Classification never forwards a method, error code/message/data,
            # rejected value or any other provider content.
            reason = "response_envelope_invalid"
            if type(message) is dict and message.get("jsonrpc") == "2.0":
                if (type(message.get("method")) is str
                        and not set(message) - {"jsonrpc", "id", "method", "params"}):
                    reason = ("server_request_unsupported" if "id" in message
                              else "notification_unsupported")
                elif (set(message) == {"jsonrpc", "id", "error"}
                      and type(message["id"]) is int and message["id"] == request_id):
                    error = message["error"]
                    if (type(error) is dict and {"code", "message"} <= set(error)
                            and not set(error) - {"code", "message", "data"}
                            and type(error["code"]) is int and type(error["message"]) is str):
                        reason = _RPC_FAILURE_REASONS.get(error["code"], "rpc_error")
            if (not isinstance(message, dict)
                    or set(message) != {"jsonrpc", "id", "result"}
                    or message["jsonrpc"] != "2.0"
                    or type(message["id"]) is not int
                    or message["id"] != request_id
                    or not isinstance(message["result"], dict)):
                raise RelayError(response_reason=reason)
            return message["result"]

    def close(self) -> bool:
        """Close input, reap the owned process, then join and close all pipes."""
        clean = True
        try:
            self.process.stdin.close()
        except (OSError, ValueError):
            clean = False
        for action in (None, self.process.terminate, self.process.kill):
            try:
                if action is not None and self.process.poll() is None:
                    action()
                self.process.wait(timeout=self.cleanup_seconds)
                break
            except subprocess.TimeoutExpired:
                continue
            except OSError:
                clean = False
        if self.process.poll() is None or self.process.returncode != 0:
            clean = False
        for worker in self.workers:
            if worker.ident is not None:
                worker.join(timeout=self.cleanup_seconds)
                if worker.is_alive():
                    clean = False
        # A reaped child normally yields EOF promptly. Nonblocking reads let a
        # defensive stop also join workers if a pipe remains open unexpectedly.
        self.stop.set()
        for worker in self.workers:
            if worker.ident is not None and worker.is_alive():
                worker.join(timeout=self.cleanup_seconds)
                if worker.is_alive():
                    clean = False
        while True:
            try:
                if self.messages.get_nowait() is not None:
                    clean = False  # No extra replies, requests or notifications.
            except queue.Empty:
                break
        for stream in (self.process.stdout, self.process.stderr):
            try:
                stream.close()
            except (OSError, ValueError):
                clean = False
        return clean and not self.failed.is_set()


class ViewRelay:
    """Per-call transport; command injection is for offline fixture tests only."""

    def __init__(self, automation_id: str, command: Sequence[str],
                 cwd: Path | str | None = None,
                 timeout_seconds: float = CALL_TIMEOUT_SECONDS,
                 cleanup_seconds: float = CLEANUP_TIMEOUT_SECONDS):
        if (not isinstance(automation_id, str) or not automation_id.strip()
                or len(automation_id) > 256
                or isinstance(command, (str, bytes)) or not command
                or any(not isinstance(part, str) or not part for part in command)
                or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 20
                or not math.isfinite(cleanup_seconds) or not 0 < cleanup_seconds <= 1):
            raise RelayError()
        self.automation_id = automation_id
        self.command = tuple(command)
        self.cwd = cwd
        self.timeout_seconds = timeout_seconds
        self.cleanup_seconds = cleanup_seconds

    def call(self, params: object) -> dict:
        child = None
        success = False
        try:
            metadata = validate_tool_call(params)
            initialization = _encode({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                      "params": {"protocolVersion": PROTOCOL_VERSION,
                                                 "capabilities": {}, "clientInfo": {
                                                     "name": "taskgov-review-wait-relay",
                                                     "version": "0.1.0"}}})
            call = _encode({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
                "name": "automation_update", "arguments": {
                    "mode": "view", "id": self.automation_id}, "_meta": metadata}})
            child = _ChildSession(self.command, self.cwd,
                                  time.monotonic() + self.timeout_seconds, self.cleanup_seconds)
            child.send(initialization)
            initialized = child.result(1)
            if (initialized.get("protocolVersion") not in SUPPORTED_PROTOCOL_VERSIONS
                    or not isinstance(initialized.get("capabilities"), dict)
                    or not isinstance(initialized["capabilities"].get("tools"), dict)
                    or not _valid_implementation(initialized.get("serverInfo"))):
                raise RelayError()
            child.send(_encode({"jsonrpc": "2.0", "method": "notifications/initialized"}))
            # The bundled wrapper discovers its tools on its first call.
            child.send(call)
            result = child.result(2)
            content = result.get("content")
            if (result.get("isError") is not False or not isinstance(content, list)
                    or not content or any(not isinstance(item, dict)
                                         or set(item) != {"type", "text"}
                                         or item["type"] != "text"
                                         or not isinstance(item["text"], str)
                                         for item in content)):
                raise RelayError()
            success = True
        except Exception:
            success = False
        finally:
            if child is not None:
                try:
                    success = child.close() and success
                except Exception:
                    success = False
        if success:
            return {"content": [{"type": "text", "text": "{}"}], "isError": False}
        return _failure()


def _valid_control_params(params: object, *, paginated: bool = False) -> bool:
    """Accept standard control metadata without retaining it as call identity."""
    if not isinstance(params, dict):
        return False
    allowed = {"_meta", "cursor"} if paginated else {"_meta"}
    if set(params) - allowed:
        return False
    if "_meta" in params and not isinstance(params["_meta"], dict):
        return False
    # This static catalogue fits in one page and never issues a cursor.
    return not paginated or params.get("cursor") is None


def serve(stdin: BinaryIO, stdout: BinaryIO, relay: ViewRelay) -> int:
    """Serve one serial JSON-lines MCP session until EOF; never launch on discovery."""
    state = "new"

    def reply(request_id: object, *, result: object = None, code: int | None = None) -> None:
        message = {"jsonrpc": "2.0", "id": request_id}
        if code is None:
            message["result"] = result
        else:
            message["error"] = {"code": code, "message": "Invalid review-wait relay request."}
        stdout.write(_encode(message))
        stdout.flush()

    while True:
        line = stdin.readline(MAX_MESSAGE_BYTES + 1)
        if not line:
            return 0
        try:
            if len(line) > MAX_MESSAGE_BYTES or not line.endswith(b"\n"):
                raise RelayError()
            request = strict_json_loads(line)
        except RelayError:
            reply(None, code=-32700)
            return 1
        if (not isinstance(request, dict) or request.get("jsonrpc") != "2.0"
                or not isinstance(request.get("method"), str)
                or set(request) - {"jsonrpc", "id", "method", "params"}):
            reply(None, code=-32600)
            continue
        method = request["method"]
        params = request.get("params", {})
        if "id" not in request:
            if (method == "notifications/initialized" and state == "initializing"
                    and _valid_control_params(params)):
                state = "ready"
            continue  # JSON-RPC notifications never receive responses or run tools.
        request_id = request["id"]
        if type(request_id) not in (str, int):
            reply(None, code=-32600)
            continue
        if method == "initialize":
            if state != "new":
                reply(request_id, code=-32600)
            elif (not isinstance(params, dict)
                  or not isinstance(params.get("protocolVersion"), str)
                  or not isinstance(params.get("capabilities"), dict)
                  or not _valid_implementation(params.get("clientInfo"))):
                reply(request_id, code=-32602)
            else:
                version = params["protocolVersion"]
                reply(request_id, result={
                    "protocolVersion": version if version in SUPPORTED_PROTOCOL_VERSIONS else PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "taskgov-review-wait-relay", "version": "0.1.0"}})
                state = "initializing"
        elif state != "ready":
            reply(request_id, code=-32600)
        elif method == "ping":
            if _valid_control_params(params):
                reply(request_id, result={})
            else:
                reply(request_id, code=-32602)
        elif method == "tools/list":
            if _valid_control_params(params, paginated=True):
                reply(request_id, result={"tools": [{
                    "name": TOOL_NAME,
                    "description": "View the fixed development review-wait reservation and return Hook JSON.",
                    "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
                    "annotations": {"readOnlyHint": True}}]})
            else:
                reply(request_id, code=-32602)
        elif method == "tools/call":
            try:
                validate_tool_call(params)
            except RelayError:
                reply(request_id, code=-32602)
            else:
                reply(request_id, result=relay.call(params))
        else:
            reply(request_id, code=-32601)


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        raise RelayError()


def main(argv: Sequence[str] | None = None) -> int:
    parser = _ArgumentParser(description=__doc__)
    parser.add_argument("--automation-id", required=True)
    parser.add_argument("--server", required=True, help="Absolute installed codex-app-tools server.mjs path")
    try:
        args = parser.parse_args(argv)
        if sys.version_info < (3, 12):
            raise RelayError()
        server = Path(args.server)
        node_value = os.environ.get("CODEX_MCP_NODE_PATH", "")
        if not node_value or not os.environ.get("CODEX_APP_TOOLS_PIPE_PATH", "").strip():
            raise RelayError()
        node = Path(node_value)
        if (not server.is_absolute() or server.name != "server.mjs" or not server.is_file()
                or not node.is_absolute() or not node.is_file()):
            raise RelayError()
        relay = ViewRelay(args.automation_id, [str(node), str(server)], cwd=server.parent)
        return serve(sys.stdin.buffer, sys.stdout.buffer, relay)
    except (Exception, KeyboardInterrupt):
        sys.stderr.write(FAILURE_TEXT + "\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
