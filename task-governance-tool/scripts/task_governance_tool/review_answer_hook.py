"""Dedicated bounded SubagentStop adapter, separate from numerical hooks.

Hook events are wake hints, never identity/completeness proof. Only an existing
admitted slot is passed to exact public recovery; unbound bodies are discarded.
"""

import json

from .review_answer import AnswerError
from .review_answer_host import _uuid
from .review_answer_collection import AnswerTurnInProgress


PENDING_RESULT = {"status": "collection_pending", "reason": "turn_in_progress"}
PENDING_MESSAGE = (
    "taskgov: review answer collection is pending because the reviewer turn is still in progress. "
    "No answer was adopted or saved; completeness, review PASS and Task completion are not established. "
    "The existing worker or explicit parent collection must confirm the completed answer."
)


def decode_event(raw):
    if type(raw) is not bytes or len(raw) > 262144:
        raise AnswerError("capacity_exceeded", reason="bound")
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError
            value[key] = item
        return value
    try:
        event = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                           parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()))
        if (type(event) is not dict or event.get("hook_event_name") != "SubagentStop"
                or type(event.get("stop_hook_active")) is not bool):
            raise ValueError
        parent = _uuid(event["session_id"])
        child = _uuid(event["agent_id"])
        turn = _uuid(event["turn_id"])
        if child == parent:
            raise ValueError
        # No last_assistant_message, path, model or provider object is retained.
        return dict(parent_session_id=parent, reviewer_session_id=child, turn_id=turn)
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, AnswerError):
        raise AnswerError("collection_unavailable", reason="shape") from None


def collect_event(raw, *, service, host):
    event = decode_event(raw)
    records = service.repository.read()
    matches = []
    for slot, record in enumerate(records, 1):
        binding = record["binding"]
        if (binding is not None and binding["parent_session_id"] == event["parent_session_id"]
                and binding["reviewer_session_id"] == event["reviewer_session_id"]):
            matches.append(slot)
    if len(matches) != 1:
        raise AnswerError("binding_mismatch", reason="identity")
    slot = matches[0]
    # Hook turn_id is not documented as the assigned child turn. It cannot
    # replace the saved source; recovery uses the admitted active attempt.
    # SubagentStop may run before the bound turn ends. Confirm publication
    # without demanding or inventing completion; registration remains separate.
    result = service.recover(slot, host, require_terminal=False)
    if type(result) is AnswerTurnInProgress:
        return dict(PENDING_RESULT)
    # Hooks require a valid neutral hook JSON result. Nothing here claims a
    # review verdict, registration, reviewer termination or Task completion.
    return {}
