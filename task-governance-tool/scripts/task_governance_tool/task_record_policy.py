"""Closed argument boundary for optional host preapproval of ordinary records.

This is a restriction, not authority. Existing validators still own Task intent,
ownership, evidence and completion. No configuration, Git write or Runner launch
is admitted here. Both the host hook and the executing entry points use it.
"""

from __future__ import annotations


class RecordPolicyError(ValueError):
    pass


COMMON = {"--repo": 1, "--json": 0, "--read-only": 0}
COMPLETION = {
    "--check": 0, "--completion-evidence-kind": 1, "--completion-revision": 1,
    "--completion-evidence-reason": 1, "--commit-not-required": 0,
    "--verification-complete": 0, "--review-complete": 0,
}
REGISTRATION = dict.fromkeys((
    "--title", "--description", "--kind", "--lane", "--order", "--priority",
    "--status", "--blocked-reason", "--review-tier", "--verification",
    "--verification-not-required", "--tags", "--contract-scope",
    "--contract-acceptance", "--contract-constraints", "--contract-authority-ref",
), 1) | {"--from-stdin": 0}
RECEIPT = dict.fromkeys((
    "--result", "--duration-ms", "--scope-coverage", "--expected-target-generation",
), 1) | {"--from-stdin": 0}
# Positional counts exclude the command words. None means one or more originals.
TASK_COMMANDS = {
    ("doctor",): ({}, 0),
    ("task", "add"): (REGISTRATION, 0),
    ("task", "list"): (dict.fromkeys(("--status", "--kind", "--lane", "--priority", "--tag", "--limit"), 1) | {"--include-done": 0}, 0),
    ("task", "next"): (dict.fromkeys(("--kind", "--lane", "--priority", "--limit"), 1) | {"--compact": 0}, 0),
    ("task", "current"): ({"--status": 1, "--limit": 1, "--compact": 0}, 0),
    ("task", "context"): ({}, 0),
    ("task", "show"): ({"--audit": 0}, 1),
    ("task", "effort"): ({}, 1),
    ("task", "checkpoint"): ({"--summary": 1, "--next-action": 1, "--unresolved-risk": 1}, 1),
    ("task", "edit"): (dict.fromkeys(("--status", "--blocked-reason", "--pause-reason", "--add-note"), 1), 1),
    ("task", "complete"): (COMPLETION, 1),
    ("handoff", "record"): ({"--summary": 1, "--rationale": 1, "--occurrence-id": 1}, 1),
    ("handoff", "list"): ({"--state": 1, "--source-task-id": 1, "--limit": 1}, 0),
    ("handoff", "show"): ({}, 1),
    ("review", "prepare"): ({"--expected-binding": 1, "--verification-receipt-id": 1}, 1),
    ("review", "target", "set"): ({"--kind": 1, "--revision": 1}, 1),
    ("review", "receipt", "add"): (dict.fromkeys(("--reviewer", "--kind", "--verdict", "--summary", "--reviewer-class", "--model-state", "--declared-model-id", "--skill-state", "--declared-skill-id", "--declared-skill-version", "--review-profile", "--review-lens", "--context-relation", "--review-method"), 1), 1),
    ("review", "result", "add"): ({}, 1),
    ("review", "finding", "add"): ({"--receipt-id": 1, "--severity": 1, "--summary": 1}, 1),
    ("review", "finding", "resolve"): ({"--resolution": 1, "--from-stdin": 0}, (0, 1)),
    ("verification", "receipt", "add"): (RECEIPT, 1),
}
HELPER_COMMANDS = {
    ("read",): ({"--packet": 1, "--material-details": 0}, 0),
    ("save",): ({"--packet": 1, "--output": 1}, 0),
    ("submit",): ({"--packet": 1}, None),
    ("prepare", "target"): ({"--directory": 1, "--reviewers": 1, "--kind": 1, "--revision": 1}, 1),
    ("prepare", "receipt"): ({"--directory": 1, "--reviewers": 1, **RECEIPT}, 1),
    ("prepare", "recover"): ({"--directory": 1, "--reviewers": 1, "--expected-binding": 1, "--verification-receipt-id": 1}, 1),
    ("material", "blob"): ({}, 1),
    ("material", "batch"): ({}, 0),
    ("material", "diff"): ({}, 2),
    ("material", "dependency"): ({"--path": 1}, 1),
    ("material", "directory"): ({"--path": 1}, 1),
    ("material", "collect"): ({"--packet": 1, "--packet-sha256": 1}, 0),
}
REPEATED = {"--unresolved-risk", "--state", "--review-profile", "--review-lens", "--review-method"}


def validate_record_arguments(script: str, argv: list[str] | tuple[str, ...]) -> str:
    """Return the explicit repository or fail before any runtime side effect.

    Exact option spellings only; no abbreviation, duplicate singleton or hidden
    second repository. Values after '=' are still values, never command words.
    Ordinary argparse performs the subsequent command-specific validation.
    """
    commands = TASK_COMMANDS if script == "taskgov.py" else HELPER_COMMANDS if script == "review_handoff.py" else {}
    if not argv or argv[0] != "--records-only":
        raise RecordPolicyError
    for command, (specific, count) in commands.items():
        options = {**(COMMON if script == "taskgov.py" else {"--repo": 1}), **specific}
        words, supplied, values = [], set(), {}
        iterator = iter(argv[1:])
        try:
            for token in iterator:
                if token == "--":
                    words.extend(iterator)
                    break
                if not token.startswith("-"):
                    words.append(token)
                    continue
                key, equal, value = token.partition("=")
                if key not in options or (key in supplied and key not in REPEATED):
                    raise RecordPolicyError
                supplied.add(key)
                if options[key] == 0:
                    if equal:
                        raise RecordPolicyError
                    values[key] = True
                else:
                    values[key] = value if equal else next(iterator)
            if tuple(words[:len(command)]) != command:
                continue
            actual = len(words) - len(command)
            if not (actual >= 1 if count is None else actual in count if isinstance(count, tuple) else actual == count):
                continue
            repo = values.get("--repo")
            if not isinstance(repo, str) or not repo:
                raise RecordPolicyError
            if command == ("task", "edit") and values.get("--status") not in (None, "ready", "in_progress", "review_pending", "paused", "blocked"):
                raise RecordPolicyError
            return repo
        except (StopIteration, RecordPolicyError):
            continue
    raise RecordPolicyError
