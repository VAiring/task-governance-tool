"""Bounded self-read transport for an already admitted reviewer hook.

This host keeps the genuine executor metadata. It shares the existing relay,
but cannot dispatch a wait, timer, message, parent read or arbitrary thread read.
The original parent belongs to saved collection admission, not this executor.
"""

from .review_wait_runtime.review_wait_host import (
    PublicMcpHost, HostAdapterError, admit_executor_metadata, executor_turn_id, _uuid,
)
from .review_wait_runtime import review_wait_mcp_relay as protocol


def hook_executor_turn_id(metadata):
    """Keep supplied turn checks; local hooks may carry only a genuine thread.

    Absence does not supply source identity: hook recovery must independently
    admit and pin an existing assignment. Never use this for fresh dispatch.
    """
    try:
        return executor_turn_id(metadata)
    except HostAdapterError as error:
        if (error.code == "executor_context_required"
                and error.executor_context == "turn_missing"):
            return None
        raise


class AnswerReadHost(PublicMcpHost):
    def __init__(self, *, metadata, reviewer_id, reviewer_turn_id, admit,
                 automation_id, codex_home, confirmed_timezone, timezone_source,
                 command, cwd=None, timeout_seconds=20, timezone_reader=None):
        try:
            self._metadata = protocol.strict_json_loads(protocol._encode(admit_executor_metadata(metadata)))
            reviewer = _uuid(reviewer_id, "caller_mismatch")
            turn = None if reviewer_turn_id is None else _uuid(reviewer_turn_id, "caller_mismatch")
            caller_turn = hook_executor_turn_id(self._metadata)
            if (self._metadata["threadId"] != reviewer
                    or (turn is not None and caller_turn is not None and caller_turn != turn)):
                raise HostAdapterError("caller_mismatch")
            if not callable(admit):
                raise HostAdapterError("invalid_host_configuration")
            self._admit = admit
            # The inherited relay correlates its executor using this field.
            # Never replace it with the collection's original parent identity.
            self._parent_id = reviewer
            self._reviewers = (reviewer,)
            self._read_arguments = dict(threadId=reviewer, hostId="local", turnLimit=2,
                includeOutputs=False, maxOutputCharsPerItem=256)
            self._configure_transport(automation_id=automation_id, codex_home=codex_home,
                confirmed_timezone=confirmed_timezone, timezone_source=timezone_source,
                command=command, cwd=cwd, timeout_seconds=timeout_seconds, timezone_reader=timezone_reader)
        except HostAdapterError:
            raise
        except Exception:
            raise HostAdapterError("invalid_host_configuration") from None

    def _admit_read(self, name, arguments):
        if (name != "read_thread" or type(arguments) is not dict
                or set(arguments) != set(self._read_arguments)
                or any(type(arguments[key]) is not type(value) or arguments[key] != value
                       for key, value in self._read_arguments.items())):
            raise HostAdapterError("unsupported_host_operation")
        self._admit()

    def _request(self, name, arguments, *, timeout=None):
        self._admit_read(name, arguments)
        return super()._request(name, arguments, timeout=timeout)

    def _tool_call_bytes(self, name, arguments):
        # Recheck the saved source and current basis immediately before dispatch,
        # after helper initialization as well as before launching that helper.
        self._admit_read(name, arguments)
        return super()._tool_call_bytes(name, arguments)
