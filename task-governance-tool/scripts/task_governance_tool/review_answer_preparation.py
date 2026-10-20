"""Opted-in request selection and exact saved-protocol lookup.

Old Packets remain byte-for-byte unchanged. The immutable preparation owns the
answer protocol and request digest; an inspection never upgrades an old request.
"""

from contextlib import closing
from hashlib import sha256
import json

from . import review_handoff_transport as files
from .review_handoff_scope import resolve
from .review_preparation_repository import PreparationRepository
from .review_wait_basis_repository import read_review_basis, read_wait_basis
from .storage import connect_initialized_readonly, current_schema_version
from .state_paths import path_lexically_exists
from .task_values import validate_task_id


PROTOCOL = "review-answer-v1"
ATTESTATION = "assigned-independent-review-v1"
INSTRUCTIONS = (
    "Review the complete exact target under the current governing authority. Inspect every changed entry and its complete diff, then the affected context, dependencies and governing sections needed for judgment. Complete before/after file bodies are not a blanket prerequisite; follow project mandatory reading rules and expand to full files when needed. Use the supplied material and verification guidance; recover only missing required fixed-target content.",
    "Return one final JSON object with exactly verdict, summary, findings, checks, limitations. Do not save a result file, submit a Receipt or include Taskgov bookkeeping.",
    "verdict is pass, changes_requested or unable_to_review. summary is a nonempty sanitized explanation of at most 1000 characters. findings is an array of at most 64 objects, each with severity (high, medium or low) and a nonempty summary of at most 1000 characters. Preserve every finding.",
    "checks and limitations are arrays of at most 32 nonempty sanitized strings each, at most 1000 characters per string. Describe checks actually performed and actual limitations; an empty array explicitly states no entries. Do not include commands, logs, secrets, prompts or transcripts.",
    "Returning pass or changes_requested attests that you actually performed this assigned review in an independent reviewer role, rather than as its implementer doing self-review. If you cannot affirm that role or sufficient review, return unable_to_review and explain why. Preserve any concrete findings already obtained.",
    "Return JSON alone. No extra fields, mixed prose or shortened findings. If the fixed material or identity cannot be recovered, use unable_to_review rather than guessing or altering the target.",
)


def answer_request(read_command, task_id):
    arguments = json.dumps({"task_id": validate_task_id(task_id)}, separators=(",", ":"))
    return ("You are assigned an independent review of the complete exact target under current project authority. "
        "Before the fixed read, use your own tool context to call the configured taskgov_review_wait server's "
        "review_wait_inspect once with " + arguments + ", if available. "
        "Reuse its available tool definition; discover only this tool if needed. This read-only connection preparation "
        "does not bind a reviewer, start waiting or prove Hook success. An absent wait is expected in a new child. "
        "If unavailable or failed, continue the review and state the limitation without raw diagnostics. "
        "Do not retry, reconnect, change settings, call collection, or request another parent message for this step. "
        "Do not repeat it when rereading material. "
        "Run the fixed read below and follow its supplied material and verification guidance. "
        "Follow explicit section/page/range commands until required content is complete.\n\nRead command:\n"
        + read_command + "\n\nFinal answer:\n" + "\n".join(INSTRUCTIONS))


def selected(repo, packet):
    from .setup_feature_config import answer_collection_allowed
    resolution = resolve(repo, writing=False)
    if not answer_collection_allowed(resolution.paths.skill_root):
        return False
    if (packet["task"]["review_tier"] == 0
        or packet.get("review_session_context", {}).get("execution_id") is None):
        return False
    with closing(connect_initialized_readonly(resolution.target)) as connection:
        if current_schema_version(connection) < 30:
            files.fail("setup_required")
    return True


def locate(repo, packet_path, packet_raw, packet):
    """Return only an exact issued collection preparation, never a new choice."""
    if packet.get("review_session_context", {}).get("execution_id") is None:
        return None
    resolution = resolve(repo, writing=False)
    with closing(connect_initialized_readonly(resolution.target)) as connection:
        if current_schema_version(connection) < 30:
            return None
        locator = read_wait_basis(connection, project_id=resolution.target.project.project_id,
            task_id=packet["task"]["task_id"])
    path = resolution.paths.review_preparation_store(locator["parent_thread_id"], locator["task_id"],
        packet["review_target"]["generation"])
    if not path_lexically_exists(path):
        return None
    records, current = PreparationRepository(path).read()
    matches = [row for row in records if row.packet_path == packet_path
        and row.packet_digest == sha256(packet_raw).hexdigest()]
    if len(matches) > 1:
        files.fail("review_continuation_conflict")
    if not matches or matches[0].version == 1:
        return None
    with closing(connect_initialized_readonly(resolution.target)) as connection:
        basis = read_review_basis(connection, project_id=resolution.target.project.project_id,
            task_id=packet["task"]["task_id"])
    record = matches[0]
    if record.basis != basis or current is None or record.preparation_id != current.preparation_id:
        files.fail("review_packet_stale")
    return resolution, record


def reviewer_view(repo, packet_path, packet_raw, packet, view):
    if packet.get("review_session_context", {}).get("version") == 2:
        from .review_answer_continuation_preparation import locate_continuation
        found = locate_continuation(repo, packet_path, packet_raw, packet)
    else:
        found = locate(repo, packet_path, packet_raw, packet)
    if found is None:
        return view
    return {**view, "result_instructions": list(INSTRUCTIONS),
        "result_template": {"verdict": None, "summary": None, "findings": [], "checks": [], "limitations": []},
        "required_output": ["One complete five-field JSON final answer; no save or submission operation."]}
