"""Closed structural audit for explicit unregistered privacy rejection recovery."""
from . import review_answer_values as values

REJECTION = dict(field=str, verdict=str, severities=[str])
SOURCE = dict(source_continuation_id=values.nullable(str), slot=int,
    reviewer_session_id=str, turn_id=str, message_id=str,
    registration=values.REGISTRATION, rejection=values.nullable(REJECTION))
REPLACEMENT = dict(version=int, source_continuation_id=values.nullable(str), sources=[SOURCE])


def validate(value):
    result = values.prepare(value, REPLACEMENT)
    values.require(result["version"] == 1)
    prior = result["source_continuation_id"]
    if prior is not None:
        values.identifier(prior, "tg_review_continuation_")
    sources = result["sources"]
    values.require(1 <= len(sources) <= 8, "bound")
    actors = []
    rejected = 0
    for source in sources:
        values.integer(source["slot"], 1, 8)
        for key in ("reviewer_session_id", "turn_id"):
            values.session(source[key])
        values.host_id(source["message_id"])
        actors.append(source["reviewer_session_id"])
        if source["source_continuation_id"] is not None:
            values.identifier(source["source_continuation_id"], "tg_review_continuation_")
        reg = source["registration"]
        values.require(reg["state"] in ("not_started", "dispatched", "unknown")
            and reg["receipt_id"] is None
            and (reg["state"] == "not_started") == (reg["submission_digest"] is None), "identity")
        if reg["submission_digest"] is not None:
            values.digest(reg["submission_digest"])
        failure = source["rejection"]
        if failure is not None:
            rejected += 1
            values.require(failure["field"] in ("summary", "findings[].summary"))
            values.require(failure["verdict"] in ("pass", "changes_requested", "unable_to_review"))
            values.require(len(failure["severities"]) <= 64
                and all(s in ("high", "medium", "low") for s in failure["severities"]), "bound")
    values.require(rejected > 0 and actors == sorted(set(actors)), "identity")
    return result


def matches(source, metadata):
    context = metadata["basis"]["continuation"]
    return (source["source_continuation_id"] == (context["continuation_id"] if context else None)
        and source["slot"] == metadata["binding"]["slot"]
        and all(source[key] == metadata["source_identity"][key]
                for key in ("reviewer_session_id", "turn_id", "message_id")))
