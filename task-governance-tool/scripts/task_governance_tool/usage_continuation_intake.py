"""Closed admitted Hook metadata retained until numerical intake commits.

Keeping this intent makes a failed short numerical writer recoverable without
replaying host events. It grants no participant, source or attribution rights;
the ordinary capture and participant admission run again before body reads.
"""

from dataclasses import asdict
from .session_identity import is_session_id
from .usage_sources import (SourceLocator, validate_locator, source_hint,
                            locator_from_source, source_from_locator)
from .usage_values import UsageError


def validate(item):
    if (type(item) is not dict or set(item) != {"event", "session", "agent", "locator"}
            or type(item["event"]) is not str
            or item["event"] not in {"SessionStart", "Stop", "SubagentStop", "SessionEnd"}
            or not is_session_id(item["session"])
            or (item["event"] == "SubagentStop" and not is_session_id(item["agent"]))
            or (item["event"] != "SubagentStop" and item["agent"] is not None)):
        raise UsageError()
    locator = item["locator"]
    if locator is not None:
        try:
            value = SourceLocator(**locator)
            validate_locator(value)
            if value.thread_id != (item["agent"] or item["session"]):
                raise UsageError()
        except (TypeError, ValueError):
            raise UsageError() from None
    return item


def capture(payload, roots, project, thread):
    child = payload["hook_event_name"] == "SubagentStop"
    hint = source_hint(thread, payload.get("agent_transcript_path" if child else "transcript_path"), roots, project)
    locator = locator_from_source(hint, roots) if hint is not None else None
    return validate(dict(event=payload["hook_event_name"], session=payload["session_id"],
                         agent=thread if child else None, locator=asdict(locator) if locator else None))


def restore(item, roots, project):
    validate(item)
    child = item["event"] == "SubagentStop"
    payload = dict(hook_event_name=item["event"], session_id=item["session"], cwd=str(project))
    if child:
        payload["agent_id"] = item["agent"]
    if item["locator"] is not None:
        source = source_from_locator(SourceLocator(**item["locator"]), roots, project)
        payload["agent_transcript_path" if child else "transcript_path"] = str(source.path)
    return payload, item["agent"] or item["session"]
