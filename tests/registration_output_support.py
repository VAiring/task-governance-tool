"""Test-only access to the registration projection's same-response bodies."""


def registration_task(payload):
    data = payload["data"]
    return registration_body(data, data, "task")


def registration_body(data, entry, field):
    from task_governance_tool.tasks import PUBLIC_EVENT_FIELDS, PUBLIC_TASK_FIELDS
    body = entry[field]
    reference = body.get(f"{field}_ref")
    if reference is None:
        return body
    destination = {"data": data}
    for part in reference.split("/")[1:]:
        destination = destination[int(part)] if isinstance(destination, list) else destination[part]
    identity = "task_id" if field == "task" else "task_event_id"
    assert destination[identity] == body[identity]
    fields = (*PUBLIC_TASK_FIELDS, "verification_requirement", "ownership") if field == "task" else PUBLIC_EVENT_FIELDS
    return {key: value for key, value in destination.items() if key in fields}
