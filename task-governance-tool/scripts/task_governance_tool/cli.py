"""Command-line interface for task-governance-tool."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections.abc import Sequence
from contextlib import closing, nullcontext
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from task_governance_tool import __version__
from task_governance_tool.compact import (
    COMPACT_CURRENT_MAX_BYTES,
    COMPACT_NEXT_MAX_BYTES,
    CompactProjectionError,
    build_compact_current_data,
    build_compact_next_data,
    compact_current_empty_data,
    compact_next_empty_data,
)
from task_governance_tool.completion import (
    COMPLETION_CHECK_MAX_BYTES,
    CompletionRequest,
)
from task_governance_tool.completion_workflow import (
    COMPLETION_BLOCKING_CODES,
    check_completion_request,
    execute_completion_request,
)
from task_governance_tool.checkpoints import record_checkpoint
from task_governance_tool.effort import (
    EffortAdvisoryError,
    EffortProfile,
    METRIC_ORDER,
    WARNING_KEY,
    build_effort_advisory,
    load_effort_profile,
)
from task_governance_tool.handoffs import (
    HandoffError,
    list_handoffs,
    record_handoff,
    show_handoff,
    withdraw_handoff,
)
from task_governance_tool.maintenance import (
    BACKUP_WARNING_MESSAGES,
    MutationOutcome,
    VIEWER_WARNING_MESSAGES,
    run_post_commit_maintenance,
)
from task_governance_tool.storage import (
    DATABASE_BUSY_MESSAGE,
    DatabaseTarget,
    StorageError,
    begin_initialized_write,
    connect_initialized,
    connect_initialized_readonly,
    count_tasks,
    is_sqlite_busy_or_locked,
    operational_sqlite_error,
    skill_root_from_script,
)
from task_governance_tool.doctor import run_doctor
from task_governance_tool.project_scope import (
    PROJECT_STATE_MESSAGES,
    STRUCTURAL_CODES,
    inspect_project_scope,
)
from task_governance_tool.state_resolver import (
    consumer_error_code,
    resolve_project_state,
)
from task_governance_tool.selection import select_next_tasks
from task_governance_tool.reviews import (
    ReviewEvidenceError,
    add_review_finding,
    add_review_receipt,
    resolve_review_finding,
    set_requested_review_target,
)
from task_governance_tool.review_packet import (
    OVERSIZED_PACKET_MESSAGE,
    REVIEW_PACKET_MAX_BYTES,
    ReviewPacketError,
    format_review_packet_text,
    prepare_review_packet,
)
from task_governance_tool.setup import run_setup
from task_governance_tool.tasks import (
    CURRENT_STATUSES,
    TaskRepositoryError,
    TaskValidationError,
    add_task,
    build_completion_request,
    edit_task,
    list_current_tasks,
    list_tasks,
    show_task,
    validate_current_status_filter,
    validate_task_id,
)
from task_governance_tool.verification_receipts import (
    VerificationReceiptError,
    add_verification_receipt,
)
EXIT_SUCCESS = 0
EXIT_USAGE = 1
EXIT_TOOL_ERROR = 2
BOUNDED_DIAGNOSTIC_OMISSION_MESSAGE = (
    "diagnostic details omitted to satisfy the bounded output limit"
)


@dataclass(frozen=True)
class CommandContext:
    command: str
    repo: str
    repo_explicit: bool
    json_output: bool
    read_only: bool
    args: argparse.Namespace
    target_override: DatabaseTarget | None = None
    read_connection_override: sqlite3.Connection | None = field(
        default=None,
        repr=False,
        compare=False,
    )


@dataclass(frozen=True)
class CommandResult:
    ok: bool
    command: str
    data: dict[str, Any] = field(default_factory=dict)
    project_id: str | None = None
    warnings: list[dict[str, str]] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)
    text: str = ""
    exit_code: int = EXIT_SUCCESS
    mutation_outcome: MutationOutcome | None = field(
        default=None,
        compare=False,
        repr=False,
    )
    maintenance_target: DatabaseTarget | None = field(
        default=None,
        compare=False,
        repr=False,
    )

    def to_json_object(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "command": self.command,
            "project_id": self.project_id,
            "data": self.data,
            "warnings": self.warnings,
            "errors": self.errors,
        }


class TaskgovArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise CommandLineError("invalid_argument", "arguments are invalid")

    def _check_value(self, action: argparse.Action, value: Any) -> None:
        if (
            isinstance(action, RootCommandSubparsersAction)
            and value not in action.choices
        ):
            raise CommandLineError(
                "invalid_command",
                "command is not available",
                exit_code=EXIT_TOOL_ERROR,
            )
        super()._check_value(action, value)


class RootCommandSubparsersAction(argparse._SubParsersAction):
    """Marker for fixed, non-echoing root-command validation."""


class CommandLineError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        exit_code: int = EXIT_USAGE,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.exit_code = exit_code


def emit_result(
    result: CommandResult,
    *,
    json_output: bool,
    max_json_bytes: int | None = None,
) -> int:
    if json_output:
        if max_json_bytes is not None:
            result = fit_bounded_json_result(
                result,
                max_bytes=max_json_bytes,
            )
        print(
            json.dumps(
                result.to_json_object(),
                indent=2,
                sort_keys=result.command != "review.prepare",
            )
        )
    elif result.text:
        if result.command == "review.prepare" and hasattr(sys.stdout, "buffer"):
            sys.stdout.buffer.write((result.text + "\n").encode("utf-8"))
        else:
            print(result.text)
    elif result.errors:
        print(result.errors[0]["message"], file=sys.stderr)
    return result.exit_code


def serialized_json_size(result: CommandResult, data: dict[str, Any]) -> int:
    """Measure pretty JSON using the portable CRLF worst-case stdout size."""
    payload = replace(result, data=data).to_json_object()
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    return len(rendered.replace("\n", "\r\n").encode("utf-8"))


def diagnostic_identity_candidates(
    result: CommandResult,
) -> tuple[CommandResult, CommandResult]:
    return (
        result,
        replace(result, project_id=None),
    )


def fit_bounded_json_identity(
    result: CommandResult,
    data: dict[str, Any],
    *,
    max_bytes: int,
) -> CommandResult:
    """Drop only diagnostic identity values that would break a hard cap."""
    for candidate in diagnostic_identity_candidates(result):
        if serialized_json_size(candidate, data) <= max_bytes:
            return candidate
    raise CompactProjectionError(
        "bounded envelope cannot fit after diagnostic identity removal"
    )


def bounded_error_code(result: CommandResult) -> str:
    if not result.errors:
        return "internal_error"
    code = str(result.errors[0].get("code", "internal_error"))
    if (
        1 <= len(code) <= 64
        and all(
            character.islower() or character.isdigit() or character == "_"
            for character in code
        )
    ):
        return code
    return "internal_error"


def fit_bounded_json_result(
    result: CommandResult,
    *,
    max_bytes: int,
) -> CommandResult:
    """Enforce one final JSON cap, sanitizing only oversized diagnostics."""
    for candidate in diagnostic_identity_candidates(result):
        if serialized_json_size(candidate, candidate.data) <= max_bytes:
            return candidate

    if result.errors:
        sanitized = replace(
            result,
            errors=[
                {
                    "code": bounded_error_code(result),
                    "message": BOUNDED_DIAGNOSTIC_OMISSION_MESSAGE,
                }
            ],
        )
        for candidate in diagnostic_identity_candidates(sanitized):
            if serialized_json_size(candidate, candidate.data) <= max_bytes:
                return candidate

    emergency = CommandResult(
        ok=False,
        command=result.command,
        project_id=None,
        data={},
        errors=[
            {
                "code": "internal_error",
                "message": "bounded output could not be rendered",
            }
        ],
        exit_code=EXIT_TOOL_ERROR,
    )
    return emergency


def error_result(
    command: str,
    code: str,
    message: str,
    exit_code: int,
    *,
    project_id: str | None = None,
) -> CommandResult:
    return CommandResult(
        ok=False,
        command=command,
        project_id=project_id,
        errors=[{"code": code, "message": message}],
        exit_code=exit_code,
    )


def success_result(command: str, text: str, data: dict[str, Any] | None = None) -> CommandResult:
    return CommandResult(
        ok=True,
        command=command,
        data=data or {},
        text=text,
        exit_code=EXIT_SUCCESS,
    )


def add_common_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--repo",
        default=argparse.SUPPRESS,
        help="target project root; defaults to current directory",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="emit machine-readable JSON",
    )
    parser.add_argument(
        "--read-only",
        action="store_true",
        default=argparse.SUPPRESS,
        help="prohibit database creation, migration, or writes",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = TaskgovArgumentParser(
        prog="taskgov",
        description="Local project task-state helper for Codex.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    add_common_options(parser)
    parser.register(
        "action",
        "root_command_parsers",
        RootCommandSubparsersAction,
    )

    subparsers = parser.add_subparsers(
        dest="command",
        action="root_command_parsers",
    )

    setup_parser = subparsers.add_parser(
        "setup",
        help="initialize, migrate, and configure local project state",
        description=(
            "Initialize, migrate, and configure local project state. "
            "For relocation, first use --read-only to preview; submit the "
            "exact token only after explicit current user approval."
        ),
        epilog=(
            "Relocation errors: project_relocation_required, "
            "relocation_token_invalid, relocation_token_expired, "
            "relocation_token_stale, relocation_token_used, "
            "relocation_not_required."
        ),
    )
    add_common_options(setup_parser)
    setup_parser.add_argument(
        "--backup-interval-minutes",
        type=int,
        default=None,
    )
    setup_parser.add_argument(
        "--backup-generations",
        type=int,
        default=None,
    )
    setup_parser.add_argument(
        "--confirm-relocation",
        default=None,
        metavar="TOKEN",
        help=(
            "submit the exact unexpired token only after a current "
            "setup --read-only relocation preview and explicit user approval"
        ),
    )

    doctor_parser = subparsers.add_parser(
        "doctor",
        help="inspect package and project readiness without writing",
    )
    add_common_options(doctor_parser)

    task_parser = subparsers.add_parser("task", help="task commands")
    task_subparsers = task_parser.add_subparsers(dest="task_command")
    task_add_parser = task_subparsers.add_parser("add", help="register an explicit task")
    add_common_options(task_add_parser)
    task_add_parser.add_argument("--title", default="")
    task_add_parser.add_argument("--description", default="")
    task_add_parser.add_argument("--kind", default="optional")
    task_add_parser.add_argument("--lane", default="")
    task_add_parser.add_argument("--order", dest="lane_order", default=None)
    task_add_parser.add_argument("--priority", default="normal")
    task_add_parser.add_argument("--status", default="ready")
    task_add_parser.add_argument("--blocked-reason", default="")
    task_add_parser.add_argument("--review-tier", default=1)
    task_add_parser.add_argument("--verification", default="")
    task_add_parser.add_argument("--tags", default="")
    task_add_parser.add_argument("--contract-scope", default=argparse.SUPPRESS)
    task_add_parser.add_argument("--contract-acceptance", default=argparse.SUPPRESS)
    task_add_parser.add_argument("--contract-constraints", default=argparse.SUPPRESS)
    task_add_parser.add_argument("--contract-authority-ref", default=argparse.SUPPRESS)
    task_add_parser.add_argument("--contract-change-reason", default=argparse.SUPPRESS)
    task_list_parser = task_subparsers.add_parser("list", help="list compact task slices")
    add_common_options(task_list_parser)
    task_list_parser.add_argument("--status", default=None)
    task_list_parser.add_argument("--kind", default=None)
    task_list_parser.add_argument("--lane", default=None)
    task_list_parser.add_argument("--priority", default=None)
    task_list_parser.add_argument("--tag", default=None)
    task_list_parser.add_argument("--limit", default=None)
    task_list_parser.add_argument("--include-done", action="store_true", default=False)
    task_next_parser = task_subparsers.add_parser("next", help="show next actionable tasks")
    add_common_options(task_next_parser)
    task_next_parser.add_argument("--kind", default=None)
    task_next_parser.add_argument("--lane", default=None)
    task_next_parser.add_argument("--priority", default=None)
    task_next_parser.add_argument("--limit", default=None)
    task_next_parser.add_argument(
        "--compact",
        action="store_true",
        default=False,
        help="emit the bounded compact JSON projection",
    )
    task_current_parser = task_subparsers.add_parser("current", help="rediscover active or held work")
    add_common_options(task_current_parser)
    task_current_parser.add_argument("--status", default=None)
    task_current_parser.add_argument("--limit", default=None)
    task_current_parser.add_argument(
        "--compact",
        action="store_true",
        default=False,
        help="emit the bounded compact JSON projection",
    )
    task_effort_parser = task_subparsers.add_parser(
        "effort",
        help="show an optional informational effort observation",
    )
    add_common_options(task_effort_parser)
    task_effort_parser.add_argument("task_id")
    task_show_parser = task_subparsers.add_parser(
        "show",
        help="show one task, current context, and completion history",
    )
    add_common_options(task_show_parser)
    task_show_parser.add_argument("task_id")
    task_checkpoint_parser = task_subparsers.add_parser(
        "checkpoint",
        help="record an optional typed continuation checkpoint",
    )
    add_common_options(task_checkpoint_parser)
    task_checkpoint_parser.add_argument("task_id")
    task_checkpoint_parser.add_argument("--summary", required=True)
    task_checkpoint_parser.add_argument("--next-action", required=True)
    task_checkpoint_parser.add_argument(
        "--unresolved-risk",
        action="append",
        default=None,
    )
    task_edit_parser = task_subparsers.add_parser("edit", help="update task state or metadata")
    add_common_options(task_edit_parser)
    task_edit_parser.add_argument("task_id")
    task_edit_parser.add_argument("--title", default=argparse.SUPPRESS)
    task_edit_parser.add_argument("--description", default=argparse.SUPPRESS)
    task_edit_parser.add_argument("--kind", default=argparse.SUPPRESS)
    task_edit_parser.add_argument("--lane", default=argparse.SUPPRESS)
    task_edit_parser.add_argument("--order", dest="lane_order", default=argparse.SUPPRESS)
    task_edit_parser.add_argument("--priority", default=argparse.SUPPRESS)
    task_edit_parser.add_argument("--status", default=argparse.SUPPRESS)
    task_edit_parser.add_argument("--blocked-reason", default=argparse.SUPPRESS)
    task_edit_parser.add_argument("--pause-reason", default=argparse.SUPPRESS)
    task_edit_parser.add_argument("--review-tier", default=argparse.SUPPRESS)
    task_edit_parser.add_argument("--verification", default=argparse.SUPPRESS)
    task_edit_parser.add_argument("--tags", default=argparse.SUPPRESS)
    task_edit_parser.add_argument("--add-note", default=argparse.SUPPRESS)
    task_edit_parser.add_argument("--reopen-reason", default=argparse.SUPPRESS)
    task_edit_parser.add_argument(
        "--review-tier-change-reason",
        default=argparse.SUPPRESS,
    )
    task_edit_parser.add_argument("--completion-commit-hash", default=argparse.SUPPRESS)
    task_edit_parser.add_argument("--completion-evidence-kind", default=argparse.SUPPRESS)
    task_edit_parser.add_argument("--completion-revision", default=argparse.SUPPRESS)
    task_edit_parser.add_argument("--completion-evidence-reason", default=argparse.SUPPRESS)
    task_edit_parser.add_argument(
        "--external-revision-approved",
        action="store_true",
        default=argparse.SUPPRESS,
    )
    task_edit_parser.add_argument("--commit-not-required", action="store_true", default=argparse.SUPPRESS)
    task_edit_parser.add_argument("--verification-complete", action="store_true", default=argparse.SUPPRESS)
    task_edit_parser.add_argument("--review-complete", action="store_true", default=argparse.SUPPRESS)
    task_edit_parser.add_argument("--contract-scope", default=argparse.SUPPRESS)
    task_edit_parser.add_argument("--contract-acceptance", default=argparse.SUPPRESS)
    task_edit_parser.add_argument("--contract-constraints", default=argparse.SUPPRESS)
    task_edit_parser.add_argument("--contract-authority-ref", default=argparse.SUPPRESS)
    task_edit_parser.add_argument("--contract-change-reason", default=argparse.SUPPRESS)
    task_complete_parser = task_subparsers.add_parser(
        "complete",
        help="check or complete one task through the existing completion gate",
    )
    add_common_options(task_complete_parser)
    task_complete_parser.add_argument("task_id")
    task_complete_parser.add_argument(
        "--check",
        action="store_true",
        default=False,
        help="check completion readiness without writing",
    )
    task_complete_parser.add_argument(
        "--completion-evidence-kind",
        default=argparse.SUPPRESS,
    )
    task_complete_parser.add_argument(
        "--completion-revision",
        default=argparse.SUPPRESS,
    )
    task_complete_parser.add_argument(
        "--completion-evidence-reason",
        default=argparse.SUPPRESS,
    )
    task_complete_parser.add_argument(
        "--external-revision-approved",
        action="store_true",
        default=argparse.SUPPRESS,
    )
    task_complete_parser.add_argument(
        "--commit-not-required",
        action="store_true",
        default=argparse.SUPPRESS,
    )
    task_complete_parser.add_argument(
        "--verification-complete",
        action="store_true",
        default=argparse.SUPPRESS,
    )
    task_complete_parser.add_argument(
        "--review-complete",
        action="store_true",
        default=argparse.SUPPRESS,
    )

    handoff_parser = subparsers.add_parser("handoff", help="local handoff outbox commands")
    handoff_subparsers = handoff_parser.add_subparsers(dest="handoff_command")
    handoff_record_parser = handoff_subparsers.add_parser(
        "record",
        help="durably record one out-of-scope discovery",
    )
    add_common_options(handoff_record_parser)
    handoff_record_parser.add_argument("source_task_id")
    handoff_record_parser.add_argument("--summary", required=True)
    handoff_record_parser.add_argument("--rationale", default="")
    handoff_record_parser.add_argument("--occurrence-id", default=None)
    handoff_list_parser = handoff_subparsers.add_parser(
        "list",
        help="list bounded local handoff records",
    )
    add_common_options(handoff_list_parser)
    handoff_list_parser.add_argument(
        "--state",
        dest="states",
        action="append",
        default=None,
        help="select a handoff state; repeat to include multiple states",
    )
    handoff_list_parser.add_argument("--source-task-id", default=None)
    handoff_list_parser.add_argument("--limit", default=None)
    handoff_show_parser = handoff_subparsers.add_parser(
        "show",
        help="show one local handoff record",
    )
    add_common_options(handoff_show_parser)
    handoff_show_parser.add_argument("handoff_id")
    handoff_withdraw_parser = handoff_subparsers.add_parser(
        "withdraw",
        help="withdraw an undelivered pending handoff by explicit user request",
    )
    add_common_options(handoff_withdraw_parser)
    handoff_withdraw_parser.add_argument("handoff_id")
    handoff_withdraw_parser.add_argument("--reason", required=True)

    review_parser = subparsers.add_parser(
        "review",
        help="bounded review context and structured evidence commands",
    )
    review_subparsers = review_parser.add_subparsers(dest="review_entity")

    review_prepare_parser = review_subparsers.add_parser(
        "prepare",
        help="prepare bounded read-only review context",
    )
    add_common_options(review_prepare_parser)
    review_prepare_parser.add_argument("task_id")

    review_target_parser = review_subparsers.add_parser("target", help="review target commands")
    review_target_subparsers = review_target_parser.add_subparsers(dest="review_action")
    review_target_set_parser = review_target_subparsers.add_parser(
        "set", help="set and advance a task review target"
    )
    add_common_options(review_target_set_parser)
    review_target_set_parser.add_argument("task_id")
    review_target_set_parser.add_argument("--kind", required=True)
    review_target_set_parser.add_argument(
        "--revision",
        default=None,
        help="target revision; omit when --kind git_snapshot",
    )

    review_receipt_parser = review_subparsers.add_parser("receipt", help="review receipt commands")
    review_receipt_subparsers = review_receipt_parser.add_subparsers(dest="review_action")
    review_receipt_add_parser = review_receipt_subparsers.add_parser(
        "add", help="record a sanitized review receipt"
    )
    add_common_options(review_receipt_add_parser)
    review_receipt_add_parser.add_argument("task_id")
    review_receipt_add_parser.add_argument(
        "--reviewer",
        required=True,
        help=(
            "current-generation stored distinctness key; distinct strings do "
            "not prove reviewer identity, independence, or authenticated "
            "provenance"
        ),
    )
    review_receipt_add_parser.add_argument("--kind", required=True)
    review_receipt_add_parser.add_argument("--verdict", required=True)
    review_receipt_add_parser.add_argument("--summary", default="")
    review_receipt_add_parser.add_argument("--user-approved", action="store_true")

    review_finding_parser = review_subparsers.add_parser("finding", help="review finding commands")
    review_finding_subparsers = review_finding_parser.add_subparsers(dest="review_action")
    review_finding_add_parser = review_finding_subparsers.add_parser(
        "add", help="record a sanitized review finding"
    )
    add_common_options(review_finding_add_parser)
    review_finding_add_parser.add_argument("task_id")
    review_finding_add_parser.add_argument("--receipt-id", required=True)
    review_finding_add_parser.add_argument("--severity", required=True)
    review_finding_add_parser.add_argument("--summary", required=True)
    review_finding_resolve_parser = review_finding_subparsers.add_parser(
        "resolve", help="resolve a review finding while preserving its history"
    )
    add_common_options(review_finding_resolve_parser)
    review_finding_resolve_parser.add_argument("finding_id")
    review_finding_resolve_parser.add_argument("--resolution", required=True)

    verification_parser = subparsers.add_parser(
        "verification",
        help="verification evidence commands",
    )
    verification_subparsers = verification_parser.add_subparsers(
        dest="verification_entity"
    )
    verification_receipt_parser = verification_subparsers.add_parser(
        "receipt",
        help="verification receipt commands",
    )
    verification_receipt_subparsers = verification_receipt_parser.add_subparsers(
        dest="verification_action"
    )
    verification_receipt_add_parser = verification_receipt_subparsers.add_parser(
        "add",
        help="record sanitized verification evidence",
    )
    add_common_options(verification_receipt_add_parser)
    verification_receipt_add_parser.add_argument("task_id")
    verification_receipt_add_parser.add_argument("--command-label", required=True)
    verification_receipt_add_parser.add_argument("--result", required=True)
    verification_receipt_add_parser.add_argument("--duration-ms", required=True)
    verification_receipt_add_parser.add_argument("--scope-coverage", required=True)
    verification_receipt_add_parser.add_argument(
        "--expected-target-generation",
        required=True,
    )

    return parser


def command_name(args: argparse.Namespace) -> str:
    if args.command == "task" and args.task_command:
        return f"task.{args.task_command}"
    if args.command == "handoff" and args.handoff_command:
        return f"handoff.{args.handoff_command}"
    if args.command == "review" and args.review_entity:
        if args.review_entity == "prepare":
            return "review.prepare"
        if args.review_action:
            return f"review.{args.review_entity}.{args.review_action}"
    if args.command == "verification" and args.verification_entity:
        if args.verification_action:
            return (
                f"verification.{args.verification_entity}."
                f"{args.verification_action}"
            )
    if args.command:
        return args.command
    return "help"


def make_context(
    args: argparse.Namespace,
    *,
    target_override: DatabaseTarget | None = None,
) -> CommandContext:
    return CommandContext(
        command=command_name(args),
        repo=getattr(args, "repo", "."),
        repo_explicit=hasattr(args, "repo"),
        json_output=bool(getattr(args, "json", False)),
        read_only=bool(getattr(args, "read_only", False)),
        args=args,
        target_override=target_override,
    )


def resolve_context_target(context: CommandContext) -> DatabaseTarget:
    if context.target_override is not None:
        return context.target_override
    raise StorageError(
        "internal_error",
        "database target basis is unavailable",
    )


def context_read_connection(
    context: CommandContext,
    target: DatabaseTarget,
) -> Any:
    if context.read_connection_override is not None:
        return nullcontext(context.read_connection_override)
    return closing(connect_initialized_readonly(target))


def state_resolution_failure_result(
    context: CommandContext,
    *,
    code: str,
    message: str,
    project_id: str | None,
) -> CommandResult:
    command = context.command
    if command == "task.list":
        data: dict[str, Any] = {"tasks": [], "count": 0, "limit": 0}
    elif command == "task.next":
        data = (
            compact_next_empty_data()
            if bool(getattr(context.args, "compact", False))
            else task_next_empty_data()
        )
    elif command == "task.current":
        data = task_current_result_data(context)
    elif command == "task.effort":
        data = task_effort_empty_data()
    elif command == "task.show":
        data = task_show_empty_data()
    elif command == "task.checkpoint":
        data = task_checkpoint_empty_data()
    elif command == "task.edit":
        data = task_edit_empty_data()
    elif command == "task.complete":
        data = (
            task_completion_check_empty_data(
                None
            )
            if bool(getattr(context.args, "check", False))
            else task_edit_empty_data()
        )
    elif command.startswith("handoff."):
        data = handoff_empty_data(command)
    elif command.startswith("review."):
        data = review_empty_data(command)
    elif command == "verification.receipt.add":
        data = verification_receipt_empty_data()
    else:
        data = {}
    return CommandResult(
        ok=False,
        command=command,
        project_id=project_id,
        data=data,
        errors=[{"code": code, "message": message}],
        exit_code=EXIT_TOOL_ERROR,
    )


def handle_command(context: CommandContext) -> CommandResult:
    if context.command == "setup":
        return handle_setup(context)
    if context.command == "doctor":
        return handle_doctor(context)

    if context.target_override is None:
        scope_inspection = inspect_project_scope(
            repo=context.repo,
            repo_explicit=context.repo_explicit,
            script_path=cli_script_path(),
            include_runtime=False,
            include_package=False,
            include_ignore=False,
        )
        scope_issue = scope_inspection.first_issue(allowed_codes=STRUCTURAL_CODES)
        if scope_issue is not None:
            return state_resolution_failure_result(
                context,
                code=scope_issue.code,
                message=scope_issue.message,
                project_id=None,
            )
        resolution = resolve_project_state(
            skill_root=skill_root_from_script(cli_script_path()),
            repo=Path(context.repo),
            retain_read_connection=(
                context.command
                in {
                    "task.list",
                    "task.next",
                    "task.current",
                    "task.effort",
                    "task.show",
                    "handoff.list",
                    "handoff.show",
                    "review.prepare",
                }
                or (
                    context.command == "task.complete"
                    and bool(getattr(context.args, "check", False))
                )
            ),
        )
        error_code = consumer_error_code(resolution)
        if error_code is not None or resolution.target is None:
            if resolution.read_connection is not None:
                resolution.read_connection.close()
            code = error_code or "project_state_unreadable"
            message = (
                "project state is not set up; run setup first"
                if code == "db_not_initialized"
                else PROJECT_STATE_MESSAGES.get(
                    code,
                    "project state could not be read safely",
                )
            )
            return state_resolution_failure_result(
                context,
                code=code,
                message=message,
                project_id=resolution.project_id,
            )
        context = replace(
            context,
            target_override=resolution.target,
            read_connection_override=resolution.read_connection,
        )
    try:
        result = dispatch_stateful_command(context)
    finally:
        if context.read_connection_override is not None:
            context.read_connection_override.close()
    return replace(result, maintenance_target=context.target_override)


def dispatch_stateful_command(context: CommandContext) -> CommandResult:
    if context.command == "task.add":
        return handle_task_add(context)
    elif context.command == "task.list":
        return handle_task_list(context)
    elif context.command == "task.next":
        return handle_task_next(context)
    elif context.command == "task.current":
        return handle_task_current(context)
    elif context.command == "task.effort":
        return handle_task_effort(context)
    elif context.command == "task.show":
        return handle_task_show(context)
    elif context.command == "task.checkpoint":
        return handle_task_checkpoint(context)
    elif context.command == "task.edit":
        return handle_task_edit(context)
    elif context.command == "task.complete":
        return handle_task_complete(context)
    elif context.command.startswith("handoff."):
        return handle_handoff_command(context)
    elif context.command == "review.prepare":
        return handle_review_prepare(context)
    elif context.command.startswith("review."):
        return handle_review_command(context)
    elif context.command == "verification.receipt.add":
        return handle_verification_receipt_add(context)
    return error_result(
        context.command,
        "internal_error",
        f"{context.command}: handler not implemented yet",
        EXIT_TOOL_ERROR,
    )


_CLI_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "taskgov.py"


def set_cli_script_path(script_path: str | Path) -> None:
    global _CLI_SCRIPT_PATH
    script = Path(script_path).expanduser()
    if not script.is_absolute():
        script = Path.cwd() / script
    _CLI_SCRIPT_PATH = script.absolute()


def cli_script_path() -> Path:
    return _CLI_SCRIPT_PATH


def handle_setup(context: CommandContext) -> CommandResult:
    service_result = run_setup(
        repo=context.repo,
        repo_explicit=context.repo_explicit,
        script_path=cli_script_path(),
        read_only=context.read_only,
        backup_interval_minutes=getattr(
            context.args,
            "backup_interval_minutes",
            None,
        ),
        backup_generations=getattr(
            context.args,
            "backup_generations",
            None,
        ),
        confirmation_token=getattr(
            context.args,
            "confirm_relocation",
            None,
        ),
    )
    return CommandResult(
        ok=service_result.ok,
        command=context.command,
        project_id=service_result.project_id,
        data=service_result.data,
        errors=(
            [
                {
                    "code": service_result.error_code,
                    "message": service_result.error_message,
                }
            ]
            if service_result.error_code is not None
            and service_result.error_message is not None
            else []
        ),
        text=service_result.text,
        exit_code=(
            EXIT_SUCCESS if service_result.ok else EXIT_TOOL_ERROR
        ),
    )


def handle_doctor(context: CommandContext) -> CommandResult:
    service_result = run_doctor(
        repo=context.repo,
        repo_explicit=context.repo_explicit,
        script_path=cli_script_path(),
    )
    return CommandResult(
        ok=service_result.ok,
        command=context.command,
        project_id=service_result.project_id,
        data=service_result.data,
        warnings=service_result.warnings,
        errors=service_result.errors,
        text=service_result.text,
        exit_code=(
            EXIT_SUCCESS if service_result.ok else EXIT_TOOL_ERROR
        ),
    )


def task_add_input(args: argparse.Namespace) -> dict[str, Any]:
    task_input = {
        "title": getattr(args, "title", ""),
        "description": getattr(args, "description", ""),
        "kind": getattr(args, "kind", "optional"),
        "lane": getattr(args, "lane", ""),
        "lane_order": getattr(args, "lane_order", None),
        "priority": getattr(args, "priority", "normal"),
        "status": getattr(args, "status", "ready"),
        "blocked_reason": getattr(args, "blocked_reason", ""),
        "review_tier": getattr(args, "review_tier", 1),
        "verification": getattr(args, "verification", ""),
        "tags": getattr(args, "tags", ""),
    }
    for field in (
        "contract_scope",
        "contract_acceptance",
        "contract_constraints",
        "contract_authority_ref",
        "contract_change_reason",
    ):
        if hasattr(args, field):
            task_input[field] = getattr(args, field)
    return task_input


def validation_failure_result(
    context: CommandContext,
    *,
    project_id: str | None,
    exc: TaskValidationError | TaskRepositoryError,
) -> CommandResult:
    exit_code = EXIT_TOOL_ERROR if exc.code == "internal_error" else EXIT_USAGE
    return CommandResult(
        ok=False,
        command=context.command,
        project_id=project_id,
        errors=[{"code": exc.code, "message": exc.message}],
        exit_code=exit_code,
    )


def task_add_text(
    task: dict[str, Any],
    event: dict[str, Any],
    contract_write: dict[str, Any] | None = None,
) -> str:
    lines = [
        f"Task added: {task['task_id']}",
        f"Title: {task['title']}",
        f"Kind: {task['kind']}  Status: {task['status']}  Priority: {task['priority']}",
        f"Review tier: {task['review_tier']}",
        f"Event: {event['event_type']}",
    ]
    if task["kind"] == "sequential":
        lines.insert(3, f"Lane: {task['lane']}  Order: {task['lane_order']}")
    if contract_write is not None:
        lines.append(
            "Contract: "
            f"revision {contract_write['revision']} "
            f"recorded={str(contract_write['recorded']).lower()}"
        )
    return "\n".join(lines)


def handle_task_add(context: CommandContext) -> CommandResult:
    target = resolve_context_target(context)
    if context.read_only:
        return CommandResult(
            ok=False,
            command=context.command,
            project_id=target.project.project_id,
            errors=[
                {
                    "code": "invalid_argument",
                    "message": "task add cannot run with --read-only because it writes the database",
                }
            ],
            exit_code=EXIT_USAGE,
        )

    task_input = task_add_input(context.args)
    effort_profile = load_effort_profile(skill_root_from_script(cli_script_path()))
    try:
        with closing(connect_initialized(target)) as connection:
            with connection:
                result = add_task(
                    connection,
                    target.project,
                    effort_profile=effort_profile,
                    database_target=target,
                    **task_input,
                )
    except TaskValidationError as exc:
        return validation_failure_result(
            context,
            project_id=target.project.project_id,
            exc=exc,
        )
    except TaskRepositoryError as exc:
        return validation_failure_result(
            context,
            project_id=target.project.project_id,
            exc=exc,
        )
    except StorageError as exc:
        return CommandResult(
            ok=False,
            command=context.command,
            project_id=target.project.project_id,
            errors=[{"code": exc.code, "message": exc.message}],
            exit_code=EXIT_TOOL_ERROR,
        )
    except sqlite3.Error as exc:
        mapped = operational_sqlite_error(
            exc,
            fallback_message="could not add task",
        )
        return CommandResult(
            ok=False,
            command=context.command,
            project_id=target.project.project_id,
            errors=[{"code": mapped.code, "message": mapped.message}],
            exit_code=EXIT_TOOL_ERROR,
        )

    data = {"task": result.task, "event": result.event}
    if result.contract_write is not None:
        data["contract_write"] = result.contract_write
    return CommandResult(
        ok=True,
        command=context.command,
        project_id=target.project.project_id,
        data=data,
        text=task_add_text(result.task, result.event, result.contract_write),
        exit_code=EXIT_SUCCESS,
        mutation_outcome=MutationOutcome(
            state_changed=True,
            viewer_relevant=True,
        ),
    )


def task_list_input(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "status": getattr(args, "status", None),
        "kind": getattr(args, "kind", None),
        "lane": getattr(args, "lane", None),
        "priority": getattr(args, "priority", None),
        "tag": getattr(args, "tag", None),
        "limit": getattr(args, "limit", None),
        "include_done": bool(getattr(args, "include_done", False)),
    }


def task_list_text(tasks: list[dict[str, Any]], count: int, limit: int) -> str:
    lines = [f"Tasks: {count} (limit {limit})"]
    for task in tasks:
        lane = ""
        if task["lane"]:
            lane = f" {task['lane']}"
            if task["lane_order"] is not None:
                lane += f"#{task['lane_order']}"
        lines.append(
            f"{task['task_id']} [{task['status']}] {task['priority']} {task['kind']}{lane} - {task['title']}"
        )
    return "\n".join(lines)


def handle_task_list(context: CommandContext) -> CommandResult:
    target = resolve_context_target(context)
    try:
        with context_read_connection(context, target) as connection:
            result = list_tasks(connection, target.project, **task_list_input(context.args))
    except TaskValidationError as exc:
        return validation_failure_result(
            context,
            project_id=target.project.project_id,
            exc=exc,
        )
    except StorageError as exc:
        return CommandResult(
            ok=False,
            command=context.command,
            project_id=target.project.project_id,
            data={"tasks": [], "count": 0, "limit": 0},
            errors=[{"code": exc.code, "message": exc.message}],
            exit_code=EXIT_TOOL_ERROR,
        )
    except sqlite3.Error as exc:
        code = "database_busy" if _is_transient_sqlite_lock(exc) else "internal_error"
        message = (
            DATABASE_BUSY_MESSAGE
            if code == "database_busy"
            else "could not list tasks"
        )
        return CommandResult(
            ok=False,
            command=context.command,
            project_id=target.project.project_id,
            data={"tasks": [], "count": 0, "limit": 0},
            errors=[{"code": code, "message": message}],
            exit_code=EXIT_TOOL_ERROR,
        )

    data = {"tasks": result.tasks, "count": result.count, "limit": result.limit}
    return CommandResult(
        ok=True,
        command=context.command,
        project_id=target.project.project_id,
        data=data,
        text=task_list_text(result.tasks, result.count, result.limit),
        exit_code=EXIT_SUCCESS,
    )


def task_next_input(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "kind": getattr(args, "kind", None),
        "lane": getattr(args, "lane", None),
        "priority": getattr(args, "priority", None),
        "limit": getattr(args, "limit", None),
    }


def task_next_empty_data() -> dict[str, Any]:
    return {"tasks": [], "count": 0, "limit": 0, "selection_rules": {}}


def task_next_failure_result(
    context: CommandContext,
    *,
    project_id: str | None,
    code: str,
    message: str,
    exit_code: int,
) -> CommandResult:
    data = (
        compact_next_empty_data()
        if bool(getattr(context.args, "compact", False))
        else task_next_empty_data()
    )
    return CommandResult(
        ok=False,
        command=context.command,
        project_id=project_id,
        data=data,
        errors=[{"code": code, "message": message}],
        exit_code=exit_code,
    )


def task_next_text(
    tasks: list[dict[str, Any]],
    count: int,
    limit: int,
    warnings: Sequence[dict[str, str]] = (),
) -> str:
    lines = [f"Next tasks: {count} (limit {limit})"]
    for task in tasks:
        lane = ""
        if task["lane"]:
            lane = f" {task['lane']}"
            if task["lane_order"] is not None:
                lane += f"#{task['lane_order']}"
        lines.append(
            f"{task['task_id']} [{task['status']}] {task['priority']} {task['kind']}{lane} - {task['title']}"
        )
    lines.extend(f"Warning: {warning['message']}" for warning in warnings)
    return "\n".join(lines)


def handle_task_next(context: CommandContext) -> CommandResult:
    target = resolve_context_target(context)
    try:
        with context_read_connection(context, target) as connection:
            result = select_next_tasks(connection, target.project, **task_next_input(context.args))
            paused_count = count_tasks(
                connection,
                target.project.project_id,
            )["paused"]
    except TaskValidationError as exc:
        return task_next_failure_result(
            context,
            project_id=target.project.project_id,
            code=exc.code,
            message=exc.message,
            exit_code=EXIT_USAGE,
        )
    except StorageError as exc:
        return task_next_failure_result(
            context,
            project_id=target.project.project_id,
            code=exc.code,
            message=exc.message,
            exit_code=EXIT_TOOL_ERROR,
        )
    except sqlite3.Error as exc:
        code = "database_busy" if _is_transient_sqlite_lock(exc) else "internal_error"
        return task_next_failure_result(
            context,
            project_id=target.project.project_id,
            code=code,
            message=(
                DATABASE_BUSY_MESSAGE
                if code == "database_busy"
                else "could not select next tasks"
            ),
            exit_code=EXIT_TOOL_ERROR,
        )

    data = {
        "tasks": result.tasks,
        "count": result.count,
        "limit": result.limit,
        "selection_rules": result.selection_rules,
    }
    warnings = []
    paused_count = int(paused_count)
    if paused_count > 0:
        paused_summary = (
            "1 paused task exists"
            if paused_count == 1
            else f"{paused_count} paused tasks exist"
        )
        warnings.append(
            {
                "code": "paused_tasks_present",
                "message": (
                    f"{paused_summary}; "
                    "run taskgov task current --status paused"
                ),
            }
        )
    command_result = CommandResult(
        ok=True,
        command=context.command,
        project_id=target.project.project_id,
        data=data,
        warnings=warnings,
        text=task_next_text(result.tasks, result.count, result.limit, warnings),
        exit_code=EXIT_SUCCESS,
    )
    if bool(getattr(context.args, "compact", False)):
        command_result = fit_bounded_json_identity(
            command_result,
            compact_next_empty_data(
                limit=result.limit,
                total_matching=result.total_matching,
                truncated=bool(result.tasks),
            ),
            max_bytes=COMPACT_NEXT_MAX_BYTES,
        )
        try:
            compact_data = build_compact_next_data(
                result.tasks,
                total_matching=result.total_matching,
                limit=result.limit,
                serialized_size=lambda candidate: serialized_json_size(
                    command_result,
                    candidate,
                ),
            )
        except CompactProjectionError:
            return task_next_failure_result(
                context,
                project_id=target.project.project_id,
                code="internal_error",
                message="could not build compact next-task output",
                exit_code=EXIT_TOOL_ERROR,
            )
        command_result = replace(command_result, data=compact_data)
    return command_result


def task_current_empty_data(
    statuses: Sequence[str] = CURRENT_STATUSES,
) -> dict[str, Any]:
    return {
        "tasks": [],
        "count": 0,
        "limit": 0,
        "statuses": list(statuses),
    }


def task_current_result_data(
    context: CommandContext,
    statuses: Sequence[str] = CURRENT_STATUSES,
) -> dict[str, Any]:
    if bool(getattr(context.args, "compact", False)):
        return compact_current_empty_data(statuses)
    return task_current_empty_data(statuses)


def task_current_text(tasks: list[dict[str, Any]], count: int, limit: int) -> str:
    lines = [f"Current tasks: {count} (limit {limit})"]
    for task in tasks:
        lines.append(
            f"{task['task_id']} [{task['status']}] {task['priority']} - "
            f"{task['title']} | {task['suggested_next_action']}"
        )
    return "\n".join(lines)


def handle_task_current(context: CommandContext) -> CommandResult:
    target = resolve_context_target(context)
    try:
        status_filter = validate_current_status_filter(
            getattr(context.args, "status", None)
        )
    except TaskValidationError as exc:
        return CommandResult(
            ok=False,
            command=context.command,
            project_id=target.project.project_id,
            data=task_current_result_data(context),
            errors=[{"code": exc.code, "message": exc.message}],
            exit_code=EXIT_USAGE,
        )
    effective_statuses = (
        CURRENT_STATUSES if status_filter is None else (status_filter,)
    )
    try:
        with context_read_connection(context, target) as connection:
            result = list_current_tasks(
                connection,
                target.project,
                limit=getattr(context.args, "limit", None),
                status=status_filter,
            )
    except TaskValidationError as exc:
        return CommandResult(
            ok=False,
            command=context.command,
            project_id=target.project.project_id,
            data=task_current_result_data(context, effective_statuses),
            errors=[{"code": exc.code, "message": exc.message}],
            exit_code=EXIT_USAGE,
        )
    except TaskRepositoryError as exc:
        return CommandResult(
            ok=False,
            command=context.command,
            project_id=target.project.project_id,
            data=task_current_result_data(context, effective_statuses),
            errors=[{"code": exc.code, "message": exc.message}],
            exit_code=(
                EXIT_TOOL_ERROR if exc.code == "internal_error" else EXIT_USAGE
            ),
        )
    except StorageError as exc:
        return CommandResult(
            ok=False,
            command=context.command,
            project_id=target.project.project_id,
            data=task_current_result_data(context, effective_statuses),
            errors=[{"code": exc.code, "message": exc.message}],
            exit_code=EXIT_TOOL_ERROR,
        )
    except sqlite3.Error as exc:
        code = "database_busy" if _is_transient_sqlite_lock(exc) else "internal_error"
        return CommandResult(
            ok=False,
            command=context.command,
            project_id=target.project.project_id,
            data=task_current_result_data(context, effective_statuses),
            errors=[
                {
                    "code": code,
                    "message": (
                        DATABASE_BUSY_MESSAGE
                        if code == "database_busy"
                        else "could not list current tasks"
                    ),
                }
            ],
            exit_code=EXIT_TOOL_ERROR,
        )
    data = {
        "tasks": result.tasks,
        "count": result.count,
        "limit": result.limit,
        "statuses": list(result.statuses),
    }
    command_result = CommandResult(
        ok=True,
        command=context.command,
        project_id=target.project.project_id,
        data=data,
        text=task_current_text(result.tasks, result.count, result.limit),
        exit_code=EXIT_SUCCESS,
    )
    if bool(getattr(context.args, "compact", False)):
        command_result = fit_bounded_json_identity(
            command_result,
            compact_current_empty_data(
                result.statuses,
                limit=result.limit,
                total_matching=result.total_matching,
                truncated=bool(result.tasks),
            ),
            max_bytes=COMPACT_CURRENT_MAX_BYTES,
        )
        try:
            compact_data = build_compact_current_data(
                result.tasks,
                total_matching=result.total_matching,
                limit=result.limit,
                statuses=result.statuses,
                serialized_size=lambda candidate: serialized_json_size(
                    command_result,
                    candidate,
                ),
            )
        except CompactProjectionError:
            return CommandResult(
                ok=False,
                command=context.command,
                project_id=target.project.project_id,
                data=compact_current_empty_data(effective_statuses),
                errors=[
                    {
                        "code": "internal_error",
                        "message": "could not build compact current-task output",
                    }
                ],
                exit_code=EXIT_TOOL_ERROR,
            )
        command_result = replace(command_result, data=compact_data)
    return command_result


def task_effort_empty_data(task_id: str | None = None) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "enabled": False,
        "profile": {"id": None, "version": None, "hash": None},
        "measurements": {key: None for key in METRIC_ORDER},
        "thresholds": {},
        "exceeded": [],
        "basis": {
            "status": "not_captured",
            "revision": None,
            "clean": None,
            "captured_at": None,
            "activity_generation": None,
        },
        "observation": {
            "revision": None,
            "clean": None,
            "observed_at": None,
        },
        "coverage": {key: "unavailable" for key in METRIC_ORDER},
        "attribution": "unknown",
        "unknown_reasons": [],
        "warning_key": WARNING_KEY,
        "suggested_action": "continue",
    }


def task_effort_text(data: dict[str, Any]) -> str:
    enabled = "enabled" if data["enabled"] else "disabled"
    exceeded = ", ".join(data["exceeded"]) or "none"
    reasons = ", ".join(data["unknown_reasons"]) or "none"
    measurements = " ".join(
        f"{metric}={data['measurements'][metric] if data['measurements'][metric] is not None else 'unknown'}"
        for metric in METRIC_ORDER
    )
    thresholds = " ".join(
        f"{metric}={data['thresholds'][metric]}"
        for metric in METRIC_ORDER
        if metric in data["thresholds"]
    ) or "none"
    return "\n".join(
        [
            f"Effort advisory: {enabled}",
            f"Task: {data['task_id']}",
            f"Measurements: {measurements}",
            f"Thresholds: {thresholds}",
            f"Attribution: {data['attribution']}",
            f"Exceeded: {exceeded}",
            f"Unknown reasons: {reasons}",
            f"Suggested action: {data['suggested_action']}",
        ]
    )


def handle_task_effort(context: CommandContext) -> CommandResult:
    target = resolve_context_target(context)
    raw_task_id = getattr(context.args, "task_id", "")
    try:
        task_id = validate_task_id(raw_task_id)
        profile = load_effort_profile(skill_root_from_script(cli_script_path()))
        with context_read_connection(context, target) as connection:
            result = build_effort_advisory(
                connection,
                target.project,
                task_id,
                profile,
                database_target=target,
            )
    except TaskValidationError as exc:
        return validation_failure_result(
            context,
            project_id=target.project.project_id,
            exc=exc,
        )
    except EffortAdvisoryError as exc:
        return CommandResult(
            ok=False,
            command=context.command,
            project_id=target.project.project_id,
            data=task_effort_empty_data(task_id),
            errors=[{"code": exc.code, "message": exc.message}],
            exit_code=EXIT_USAGE if exc.code == "not_found" else EXIT_TOOL_ERROR,
        )
    except StorageError as exc:
        return CommandResult(
            ok=False,
            command=context.command,
            project_id=target.project.project_id,
            data=task_effort_empty_data(raw_task_id),
            errors=[{"code": exc.code, "message": exc.message}],
            exit_code=EXIT_TOOL_ERROR,
        )
    except sqlite3.Error as exc:
        code = "database_busy" if _is_transient_sqlite_lock(exc) else "internal_error"
        return CommandResult(
            ok=False,
            command=context.command,
            project_id=target.project.project_id,
            data=task_effort_empty_data(raw_task_id),
            errors=[
                {
                    "code": code,
                    "message": (
                        DATABASE_BUSY_MESSAGE
                        if code == "database_busy"
                        else "could not inspect task effort"
                    ),
                }
            ],
            exit_code=EXIT_TOOL_ERROR,
        )

    return CommandResult(
        ok=True,
        command=context.command,
        project_id=target.project.project_id,
        data=result.data,
        warnings=result.warnings,
        text=task_effort_text(result.data),
        exit_code=EXIT_SUCCESS,
    )


def task_show_text(
    task: dict[str, Any],
    events: list[dict[str, Any]],
    suggested_next_action: str,
    review_evidence: dict[str, Any],
    handoff_summary: dict[str, int],
    contract: dict[str, Any],
    completion_history: dict[str, Any],
    completion_history_latest_summary: dict[str, Any] | None,
) -> str:
    lines = [
        f"Task: {task['task_id']}",
        f"Title: {task['title']}",
        f"Status: {task['status']}  Priority: {task['priority']}  Kind: {task['kind']}",
    ]
    if task["lane"]:
        lane = f"Lane: {task['lane']}"
        if task["lane_order"] is not None:
            lane += f"  Order: {task['lane_order']}"
        lines.append(lane)
    lines.append(f"Review tier: {task['review_tier']}")
    lines.append(f"Contract revision: {contract['revision']}")
    review_target = review_evidence["target"]
    review_gate = review_evidence["gate"]
    lines.append(
        "Review evidence: "
        f"generation {review_target['generation']}, "
        f"passes {review_gate['qualifying_independent_passes']}/"
        f"{review_gate['required_independent_passes']}, "
        f"satisfied={str(review_gate['satisfied']).lower()}"
    )
    if task["verification"]:
        lines.append(f"Verification: {task['verification']}")
    if "completion_evidence_kind" in task:
        kind = task["completion_evidence_kind"]
        revision = task["completion_evidence_revision"]
        detail = f", {revision}" if revision else ""
        lines.append(f"Completion evidence: {kind}{detail}")
    elif "completion_commit_required" in task:
        if task["completion_commit_required"]:
            commit_hash = task["completion_commit_hash"] or "hash not set"
            lines.append(f"Completion commit: required, {commit_hash}")
        else:
            lines.append("Completion commit: not required")
    if task["blocked_reason"]:
        lines.append(f"Blocked: {task['blocked_reason']}")
    handoff_total = sum(handoff_summary.values())
    if handoff_total:
        lines.append(
            "Handoffs: "
            f"pending={handoff_summary['pending_handoff']} "
            f"handed_off={handoff_summary['handed_off']} "
            f"withdrawn={handoff_summary['handoff_withdrawn_by_user']}"
        )
    lines.append(
        "Completion history: "
        f"{completion_history['returned_count']}/{completion_history['total']} returned, "
        f"truncated={str(completion_history['truncated']).lower()}, "
        "legacy_history_incomplete="
        f"{str(completion_history['legacy_history_incomplete']).lower()}"
    )
    if completion_history_latest_summary is not None:
        latest_cycle = completion_history_latest_summary
        completed_at = latest_cycle["completed_at"] or "unknown"
        lines.append(
            "Latest completion cycle: "
            f"ordinal={latest_cycle['saved_cycle_ordinal']}, "
            f"{latest_cycle['origin']}/{latest_cycle['completeness']}, "
            f"completed_at={completed_at}, "
            f"evidence={latest_cycle['completion_evidence_kind']}, "
            f"target={latest_cycle['review_target_kind']}/generation "
            f"{latest_cycle['review_target_generation']}, "
            f"review_basis={latest_cycle['review_basis_kind']}"
        )
    lines.append(f"Suggested next action: {suggested_next_action}")
    if events:
        latest = events[0]
        lines.append(f"Latest event: {latest['event_type']} - {latest['summary']}")
    return "\n".join(lines)


def task_show_empty_data() -> dict[str, Any]:
    return {
        "task": None,
        "events": [],
        "suggested_next_action": "",
        "review_evidence": None,
        "verification_evidence": None,
        "handoff_summary": None,
        "contract": None,
        "latest_checkpoint": None,
        "completion_history": None,
    }


def task_show_failure_result(
    context: CommandContext,
    *,
    project_id: str,
    code: str,
    message: str,
    exit_code: int,
) -> CommandResult:
    return CommandResult(
        ok=False,
        command=context.command,
        project_id=project_id,
        data=task_show_empty_data(),
        errors=[{"code": code, "message": message}],
        exit_code=exit_code,
    )


def handle_task_show(context: CommandContext) -> CommandResult:
    target = resolve_context_target(context)
    try:
        with context_read_connection(context, target) as connection:
            result = show_task(connection, target.project, getattr(context.args, "task_id", ""))
    except TaskValidationError as exc:
        return task_show_failure_result(
            context,
            project_id=target.project.project_id,
            code=exc.code,
            message=exc.message,
            exit_code=(
                EXIT_TOOL_ERROR if exc.code == "internal_error" else EXIT_USAGE
            ),
        )
    except TaskRepositoryError as exc:
        if exc.code != "not_found":
            return task_show_failure_result(
                context,
                project_id=target.project.project_id,
                code=exc.code,
                message=exc.message,
                exit_code=(
                    EXIT_TOOL_ERROR
                    if exc.code == "internal_error"
                    else EXIT_USAGE
                ),
            )
        return task_show_failure_result(
            context,
            project_id=target.project.project_id,
            code=exc.code,
            message=exc.message,
            exit_code=EXIT_USAGE,
        )
    except VerificationReceiptError as exc:
        return task_show_failure_result(
            context,
            project_id=target.project.project_id,
            code=exc.code,
            message=exc.message,
            exit_code=EXIT_TOOL_ERROR,
        )
    except HandoffError:
        return task_show_failure_result(
            context,
            project_id=target.project.project_id,
            code="internal_error",
            message="could not show task",
            exit_code=EXIT_TOOL_ERROR,
        )
    except StorageError as exc:
        return task_show_failure_result(
            context,
            project_id=target.project.project_id,
            code=exc.code,
            message=exc.message,
            exit_code=EXIT_TOOL_ERROR,
        )
    except sqlite3.Error as exc:
        code = "database_busy" if _is_transient_sqlite_lock(exc) else "internal_error"
        return task_show_failure_result(
            context,
            project_id=target.project.project_id,
            code=code,
            message=(
                DATABASE_BUSY_MESSAGE
                if code == "database_busy"
                else "could not show task"
            ),
            exit_code=EXIT_TOOL_ERROR,
        )

    data = {
        "task": result.task,
        "events": result.events,
        "suggested_next_action": result.suggested_next_action,
        "review_evidence": result.review_evidence,
        "verification_evidence": result.verification_evidence,
        "handoff_summary": result.handoff_summary,
        "contract": result.contract,
        "latest_checkpoint": result.latest_checkpoint,
        "completion_history": result.completion_history,
    }
    effort_profile = load_effort_profile(skill_root_from_script(cli_script_path()))
    data["effort_advisory_enabled"] = bool(
        effort_profile.valid and effort_profile.enabled
    )
    warnings = []
    if effort_profile.present and not effort_profile.valid:
        warnings.append(
            {
                "code": "effort_advisory_profile_invalid",
                "message": "Effort Advisory configuration is invalid; advisory remains disabled.",
                "suggested_action": "continue",
            }
        )
    return CommandResult(
        ok=True,
        command=context.command,
        project_id=target.project.project_id,
        data=data,
        warnings=warnings,
        text=task_show_text(
            result.task,
            result.events,
            result.suggested_next_action,
            result.review_evidence,
            result.handoff_summary,
            result.contract,
            result.completion_history,
            result.completion_history_latest_summary,
        ),
        exit_code=EXIT_SUCCESS,
    )


def task_checkpoint_empty_data() -> dict[str, Any]:
    return {
        "checkpoint": None,
        "created": False,
        "replayed": False,
        "event": None,
    }


def task_checkpoint_failure_result(
    context: CommandContext,
    *,
    project_id: str,
    code: str,
    message: str,
    exit_code: int,
) -> CommandResult:
    return CommandResult(
        ok=False,
        command=context.command,
        project_id=project_id,
        data=task_checkpoint_empty_data(),
        errors=[{"code": code, "message": message}],
        exit_code=exit_code,
    )


def handle_task_checkpoint(context: CommandContext) -> CommandResult:
    target = resolve_context_target(context)
    project_id = target.project.project_id
    if context.read_only:
        return task_checkpoint_failure_result(
            context,
            project_id=project_id,
            code="invalid_argument",
            message=(
                "task checkpoint cannot run with --read-only because it writes "
                "the database"
            ),
            exit_code=EXIT_USAGE,
        )

    try:
        with closing(connect_initialized(target)) as connection:
            with connection:
                result = record_checkpoint(
                    connection,
                    target.project,
                    getattr(context.args, "task_id", ""),
                    summary=getattr(context.args, "summary", ""),
                    next_action=getattr(context.args, "next_action", ""),
                    unresolved_risks=getattr(
                        context.args,
                        "unresolved_risk",
                        None,
                    ),
                    database_target=target,
                )
    except (TaskValidationError, TaskRepositoryError) as exc:
        return task_checkpoint_failure_result(
            context,
            project_id=project_id,
            code=exc.code,
            message=exc.message,
            exit_code=(
                EXIT_TOOL_ERROR if exc.code == "internal_error" else EXIT_USAGE
            ),
        )
    except StorageError as exc:
        return task_checkpoint_failure_result(
            context,
            project_id=project_id,
            code=exc.code,
            message=exc.message,
            exit_code=EXIT_TOOL_ERROR,
        )
    except sqlite3.Error as exc:
        mapped = operational_sqlite_error(
            exc,
            fallback_message="could not record checkpoint",
        )
        return task_checkpoint_failure_result(
            context,
            project_id=project_id,
            code=mapped.code,
            message=mapped.message,
            exit_code=EXIT_TOOL_ERROR,
        )

    data = {
        "checkpoint": result.checkpoint,
        "created": result.created,
        "replayed": result.replayed,
        "event": result.event,
    }
    action = "recorded" if result.created else "replayed"
    return CommandResult(
        ok=True,
        command=context.command,
        project_id=project_id,
        data=data,
        text=(
            f"Checkpoint {result.checkpoint['checkpoint_id']}: {action} "
            f"for task {result.checkpoint['task_id']}"
        ),
        exit_code=EXIT_SUCCESS,
        mutation_outcome=MutationOutcome(
            state_changed=result.created,
            viewer_relevant=True,
        ),
    )


EDIT_ARGUMENT_FIELDS = (
    "title",
    "description",
    "kind",
    "lane",
    "lane_order",
    "priority",
    "status",
    "blocked_reason",
    "pause_reason",
    "review_tier",
    "verification",
    "tags",
    "add_note",
    "reopen_reason",
    "review_tier_change_reason",
    "completion_commit_hash",
    "completion_evidence_kind",
    "completion_revision",
    "completion_evidence_reason",
    "external_revision_approved",
    "commit_not_required",
    "verification_complete",
    "review_complete",
    "contract_scope",
    "contract_acceptance",
    "contract_constraints",
    "contract_authority_ref",
    "contract_change_reason",
)


def task_edit_empty_data() -> dict[str, Any]:
    return {"task": None, "changed_fields": [], "event": None}


def task_edit_input(args: argparse.Namespace) -> dict[str, Any]:
    return {field: getattr(args, field) for field in EDIT_ARGUMENT_FIELDS if hasattr(args, field)}


def task_edit_failure_result(
    context: CommandContext,
    *,
    project_id: str | None,
    code: str,
    message: str,
    exit_code: int,
) -> CommandResult:
    return CommandResult(
        ok=False,
        command=context.command,
        project_id=project_id,
        data=task_edit_empty_data(),
        errors=[{"code": code, "message": message}],
        exit_code=exit_code,
    )


def task_edit_text(
    task: dict[str, Any],
    changed_fields: list[str],
    event: dict[str, Any] | None,
    contract_write: dict[str, Any] | None = None,
    *,
    completed: bool = False,
) -> str:
    changed = ", ".join(changed_fields) if changed_fields else "none"
    lines = [
        (
            f"Task completed: {task['task_id']}"
            if completed
            else f"Task updated: {task['task_id']}"
        ),
        f"Title: {task['title']}",
        f"Status: {task['status']}  Priority: {task['priority']}  Kind: {task['kind']}",
        f"Changed: {changed}",
    ]
    if event is not None:
        lines.append(f"Event: {event['event_type']} - {event['summary']}")
    if contract_write is not None:
        lines.append(
            "Contract: "
            f"revision {contract_write['revision']} "
            f"recorded={str(contract_write['recorded']).lower()}"
        )
    return "\n".join(lines)


def handle_task_edit(context: CommandContext) -> CommandResult:
    target = resolve_context_target(context)
    if context.read_only:
        return task_edit_failure_result(
            context,
            project_id=target.project.project_id,
            code="invalid_argument",
            message="task edit cannot run with --read-only because it writes the database",
            exit_code=EXIT_USAGE,
        )

    edit_input = task_edit_input(context.args)
    effort_profile = load_effort_profile(skill_root_from_script(cli_script_path()))
    try:
        with closing(connect_initialized(target)) as connection:
            with connection:
                result = edit_task(
                    connection,
                    target.project,
                    getattr(context.args, "task_id", ""),
                    effort_profile=effort_profile,
                    database_target=target,
                    **edit_input,
                )
    except TaskValidationError as exc:
        return task_edit_failure_result(
            context,
            project_id=target.project.project_id,
            code=exc.code,
            message=exc.message,
            exit_code=EXIT_USAGE,
        )
    except TaskRepositoryError as exc:
        exit_code = (
            EXIT_TOOL_ERROR
            if exc.code in {"internal_error", "invalid_verification_evidence"}
            else EXIT_USAGE
        )
        return task_edit_failure_result(
            context,
            project_id=target.project.project_id,
            code=exc.code,
            message=exc.message,
            exit_code=exit_code,
        )
    except VerificationReceiptError as exc:
        return task_edit_failure_result(
            context,
            project_id=target.project.project_id,
            code=exc.code,
            message=exc.message,
            exit_code=(
                EXIT_TOOL_ERROR
                if exc.code in {"internal_error", "invalid_verification_evidence"}
                else EXIT_USAGE
            ),
        )
    except StorageError as exc:
        return task_edit_failure_result(
            context,
            project_id=target.project.project_id,
            code=exc.code,
            message=exc.message,
            exit_code=EXIT_TOOL_ERROR,
        )
    except sqlite3.Error as exc:
        mapped = operational_sqlite_error(
            exc,
            fallback_message="could not edit task",
        )
        return task_edit_failure_result(
            context,
            project_id=target.project.project_id,
            code=mapped.code,
            message=mapped.message,
            exit_code=EXIT_TOOL_ERROR,
        )

    data = {"task": result.task, "changed_fields": result.changed_fields, "event": result.event}
    if result.contract_write is not None:
        data["contract_write"] = result.contract_write
    return CommandResult(
        ok=True,
        command=context.command,
        project_id=target.project.project_id,
        data=data,
        text=task_edit_text(
            result.task,
            result.changed_fields,
            result.event,
            result.contract_write,
        ),
        exit_code=EXIT_SUCCESS,
        mutation_outcome=MutationOutcome(
            state_changed=result.event is not None,
            viewer_relevant=True,
        ),
    )


COMPLETE_ARGUMENT_FIELDS = (
    "completion_evidence_kind",
    "completion_revision",
    "completion_evidence_reason",
    "external_revision_approved",
    "commit_not_required",
    "verification_complete",
    "review_complete",
)


def task_complete_input(args: argparse.Namespace) -> dict[str, Any]:
    return {
        field: getattr(args, field)
        for field in COMPLETE_ARGUMENT_FIELDS
        if hasattr(args, field)
    }


def task_completion_check_empty_data(
    task_id: str | None = None,
) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "ready": False,
        "status": "",
        "blocking_codes": [],
        "contract_revision": 0,
        "review_target_generation": 0,
        "completion_evidence_kind": "none",
        "suggested_action": "inspect the command error before retrying",
    }


def task_completion_check_data(
    *,
    request: Any,
    basis: Any,
    plan: Any | None,
    blocking_code: str | None,
) -> dict[str, Any]:
    ready = blocking_code is None
    if ready:
        suggested_action = (
            "run task complete with the same evidence and confirmations"
        )
    elif blocking_code == "completion_check_stale":
        suggested_action = "run task complete --check again"
    else:
        suggested_action = f"resolve {blocking_code} before completing the task"
    evidence_kind = (
        plan.resolution.completion_evidence_kind
        if plan is not None
        else request.completion_evidence_kind
        or str(basis.task["completion_evidence_kind"])
    )
    return {
        "task_id": request.task_id,
        "ready": ready,
        "status": str(basis.task["status"]),
        "blocking_codes": [] if ready else [str(blocking_code)],
        "contract_revision": int(basis.task["current_contract_revision"]),
        "review_target_generation": int(
            basis.task["review_target_generation"]
        ),
        "completion_evidence_kind": evidence_kind,
        "suggested_action": suggested_action,
    }


def task_completion_check_text(data: dict[str, Any]) -> str:
    blocking = ",".join(data["blocking_codes"]) or "none"
    readiness = "ready" if data["ready"] else "not ready"
    return "\n".join(
        [
            f"Task {data['task_id']}: {readiness}",
            f"Blocking: {blocking}",
            f"Suggested action: {data['suggested_action']}",
        ]
    )


def task_complete_failure_result(
    context: CommandContext,
    *,
    project_id: str | None,
    task_id: str | None,
    code: str,
    message: str,
    exit_code: int,
) -> CommandResult:
    data = (
        task_completion_check_empty_data(task_id)
        if bool(getattr(context.args, "check", False))
        else task_edit_empty_data()
    )
    return CommandResult(
        ok=False,
        command=context.command,
        project_id=project_id,
        data=data,
        errors=[{"code": code, "message": message}],
        exit_code=exit_code,
    )


def completion_domain_error_result(
    context: CommandContext,
    *,
    project_id: str | None,
    task_id: str | None,
    exc: TaskValidationError | TaskRepositoryError,
) -> CommandResult:
    exit_code = (
        EXIT_TOOL_ERROR
        if isinstance(exc, TaskRepositoryError)
        and exc.code in {"internal_error", "invalid_verification_evidence"}
        else EXIT_USAGE
    )
    return task_complete_failure_result(
        context,
        project_id=project_id,
        task_id=task_id,
        code=exc.code,
        message=exc.message,
        exit_code=exit_code,
    )


def handle_task_complete(context: CommandContext) -> CommandResult:
    raw_task_id = getattr(context.args, "task_id", "")
    check_only = bool(getattr(context.args, "check", False))
    input_preflight_error: TaskValidationError | TaskRepositoryError | None = None
    try:
        request = build_completion_request(
            raw_task_id,
            **task_complete_input(context.args),
        )
    except (TaskValidationError, TaskRepositoryError) as exc:
        if exc.code in COMPLETION_BLOCKING_CODES:
            try:
                task_id = validate_task_id(raw_task_id)
            except TaskValidationError as task_id_error:
                return completion_domain_error_result(
                    context,
                    project_id=None,
                    task_id=None,
                    exc=task_id_error,
                )
            request = CompletionRequest(
                task_id=task_id,
                verification_complete=bool(
                    getattr(context.args, "verification_complete", False)
                ),
                review_complete=bool(
                    getattr(context.args, "review_complete", False)
                ),
            )
            input_preflight_error = exc
        else:
            return completion_domain_error_result(
                context,
                project_id=None,
                task_id=None,
                exc=exc,
            )

    target = resolve_context_target(context)
    if context.read_only and not check_only:
        return task_complete_failure_result(
            context,
            project_id=target.project.project_id,
            task_id=request.task_id,
            code="invalid_argument",
            message=(
                "task complete cannot run with --read-only unless --check "
                "is supplied"
            ),
            exit_code=EXIT_USAGE,
        )

    try:
        if check_only:
            outcome = check_completion_request(
                target,
                request,
                input_error=input_preflight_error,
                initial_connection=context.read_connection_override,
            )
            data = task_completion_check_data(
                request=request,
                basis=outcome.basis,
                plan=outcome.plan,
                blocking_code=outcome.blocking_code,
            )
            command_result = CommandResult(
                ok=True,
                command=context.command,
                project_id=target.project.project_id,
                data=data,
                text=task_completion_check_text(data),
                exit_code=EXIT_SUCCESS,
            )
            return command_result

        effort_profile = load_effort_profile(
            skill_root_from_script(cli_script_path())
        )
        result = execute_completion_request(
            target,
            request,
            effort_profile=effort_profile,
            input_error=input_preflight_error,
        )
    except (TaskValidationError, TaskRepositoryError) as exc:
        return completion_domain_error_result(
            context,
            project_id=target.project.project_id,
            task_id=request.task_id,
            exc=exc,
        )
    except VerificationReceiptError as exc:
        return task_complete_failure_result(
            context,
            project_id=target.project.project_id,
            task_id=request.task_id,
            code=exc.code,
            message=exc.message,
            exit_code=(
                EXIT_TOOL_ERROR
                if exc.code in {"internal_error", "invalid_verification_evidence"}
                else EXIT_USAGE
            ),
        )
    except StorageError as exc:
        return task_complete_failure_result(
            context,
            project_id=target.project.project_id,
            task_id=request.task_id,
            code=exc.code,
            message=exc.message,
            exit_code=EXIT_TOOL_ERROR,
        )
    except sqlite3.Error as exc:
        mapped = operational_sqlite_error(
            exc,
            fallback_message=(
                "could not inspect completion readiness"
                if check_only
                else "could not complete task"
            ),
        )
        return task_complete_failure_result(
            context,
            project_id=target.project.project_id,
            task_id=request.task_id,
            code=mapped.code,
            message=mapped.message,
            exit_code=EXIT_TOOL_ERROR,
        )

    data = {
        "task": result.task,
        "changed_fields": result.changed_fields,
        "event": result.event,
    }
    return CommandResult(
        ok=True,
        command=context.command,
        project_id=target.project.project_id,
        data=data,
        text=task_edit_text(
            result.task,
            result.changed_fields,
            result.event,
            completed=True,
        ),
        exit_code=EXIT_SUCCESS,
        mutation_outcome=MutationOutcome(
            state_changed=True,
            viewer_relevant=True,
        ),
    )


def handoff_empty_data(command: str) -> dict[str, Any]:
    if command == "handoff.record":
        return {
            "handoff": None,
            "local_record": {
                "durable": False,
                "created": False,
                "replayed": False,
                "handoff_id": None,
            },
        }
    if command == "handoff.list":
        return {
            "handoffs": [],
            "count": 0,
            "total_matching": 0,
            "limit": 0,
            "states": [],
        }
    if command == "handoff.withdraw":
        return {"handoff": None, "changed_fields": []}
    return {"handoff": None}


def handoff_failure_result(
    context: CommandContext,
    *,
    project_id: str,
    code: str,
    message: str,
    exit_code: int,
) -> CommandResult:
    return CommandResult(
        ok=False,
        command=context.command,
        project_id=project_id,
        data=handoff_empty_data(context.command),
        errors=[{"code": code, "message": message}],
        exit_code=exit_code,
    )


def handoff_text(command: str, data: dict[str, Any]) -> str:
    if command == "handoff.record":
        handoff = data["handoff"]
        local = data["local_record"]
        action = "recorded" if local["created"] else "replayed"
        return "\n".join(
            [
                f"Handoff {action}: {handoff['handoff_id']}",
                f"Source task: {handoff['source_task_id']}",
                f"State: {handoff['state']}",
                f"Summary: {handoff['summary']}",
            ]
        )
    if command == "handoff.list":
        lines = [
            f"Handoffs: {data['count']} of {data['total_matching']} "
            f"(limit {data['limit']})"
        ]
        for handoff in data["handoffs"]:
            lines.append(
                f"{handoff['handoff_id']} [{handoff['state']}] "
                f"{handoff['source_task_id']} - {handoff['summary']}"
            )
        return "\n".join(lines)
    if command == "handoff.withdraw":
        handoff = data["handoff"]
        return "\n".join(
            [
                f"Handoff withdrawn: {handoff['handoff_id']}",
                f"State: {handoff['state']}",
                f"Reason: {handoff['withdraw_reason']}",
            ]
        )
    handoff = data["handoff"]
    return "\n".join(
        [
            f"Handoff: {handoff['handoff_id']}",
            f"Source task: {handoff['source_task_id']}",
            f"State: {handoff['state']}",
            f"Summary: {handoff['summary']}",
            f"Created: {handoff['created_at']}",
        ]
    )


def _is_transient_sqlite_lock(exc: sqlite3.Error) -> bool:
    return is_sqlite_busy_or_locked(exc)


def _handle_handoff_record(
    context: CommandContext,
    *,
    target: Any,
    project_id: str,
) -> CommandResult:
    result = None
    for attempt in range(2):
        try:
            with closing(connect_initialized(target)) as connection:
                begin_initialized_write(connection, target)
                result = record_handoff(
                    connection,
                    target.project,
                    getattr(context.args, "source_task_id", ""),
                    summary=getattr(context.args, "summary", ""),
                    rationale=getattr(context.args, "rationale", ""),
                    occurrence_id=getattr(context.args, "occurrence_id", ""),
                )
                connection.commit()
            break
        except (TaskValidationError, TaskRepositoryError, HandoffError) as exc:
            exit_code = (
                EXIT_TOOL_ERROR
                if exc.code in {"internal_error", "handoff_not_persisted"}
                else EXIT_USAGE
            )
            return handoff_failure_result(
                context,
                project_id=project_id,
                code=exc.code,
                message=exc.message,
                exit_code=exit_code,
            )
        except StorageError as exc:
            if attempt == 0 and exc.code == "database_busy":
                continue
            return handoff_failure_result(
                context,
                project_id=project_id,
                code=exc.code,
                message=exc.message,
                exit_code=EXIT_TOOL_ERROR,
            )
        except sqlite3.Error as exc:
            if attempt == 0 and _is_transient_sqlite_lock(exc):
                continue
            mapped = operational_sqlite_error(
                exc,
                fallback_message="local handoff could not be persisted",
            )
            return handoff_failure_result(
                context,
                project_id=project_id,
                code=(
                    mapped.code
                    if mapped.code == "database_busy"
                    else "handoff_not_persisted"
                ),
                message=(
                    mapped.message
                    if mapped.code == "database_busy"
                    else "local handoff could not be persisted"
                ),
                exit_code=EXIT_TOOL_ERROR,
            )
    if result is None:
        return handoff_failure_result(
            context,
            project_id=project_id,
            code="handoff_not_persisted",
            message="local handoff could not be persisted",
            exit_code=EXIT_TOOL_ERROR,
        )
    data = {
        "handoff": result.handoff,
        "local_record": {
            "durable": True,
            "created": result.created,
            "replayed": result.replayed,
            "handoff_id": result.handoff["handoff_id"],
        },
    }
    return CommandResult(
        ok=True,
        command=context.command,
        project_id=project_id,
        data=data,
        text=handoff_text(context.command, data),
        exit_code=EXIT_SUCCESS,
        mutation_outcome=MutationOutcome(
            state_changed=bool(result.created),
            viewer_relevant=False,
        ),
    )


def handle_handoff_command(context: CommandContext) -> CommandResult:
    target = resolve_context_target(context)
    project_id = target.project.project_id
    if context.command in {"handoff.record", "handoff.withdraw"} and context.read_only:
        return handoff_failure_result(
            context,
            project_id=project_id,
            code="invalid_argument",
            message=(
                f"{context.command.replace('.', ' ')} cannot run with --read-only "
                "because it writes the database"
            ),
            exit_code=EXIT_USAGE,
        )
    if context.command == "handoff.record":
        return _handle_handoff_record(
            context,
            target=target,
            project_id=project_id,
        )

    if context.command in {"handoff.list", "handoff.show"}:
        try:
            with context_read_connection(context, target) as connection:
                if context.command == "handoff.list":
                    result = list_handoffs(
                        connection,
                        target.project,
                        states=getattr(context.args, "states", None),
                        source_task_id=getattr(context.args, "source_task_id", None),
                        limit=getattr(context.args, "limit", None),
                    )
                    data = {
                        "handoffs": result.handoffs,
                        "count": result.count,
                        "total_matching": result.total_matching,
                        "limit": result.limit,
                        "states": list(result.states),
                    }
                else:
                    handoff = show_handoff(
                        connection,
                        target.project,
                        getattr(context.args, "handoff_id", ""),
                    )
                    data = {"handoff": handoff}
        except (TaskValidationError, HandoffError) as exc:
            return handoff_failure_result(
                context,
                project_id=project_id,
                code=exc.code,
                message=exc.message,
                exit_code=(
                    EXIT_TOOL_ERROR if exc.code == "internal_error" else EXIT_USAGE
                ),
            )
        except StorageError as exc:
            return handoff_failure_result(
                context,
                project_id=project_id,
                code=exc.code,
                message=exc.message,
                exit_code=EXIT_TOOL_ERROR,
            )
        except sqlite3.Error as exc:
            code = "database_busy" if _is_transient_sqlite_lock(exc) else "internal_error"
            return handoff_failure_result(
                context,
                project_id=project_id,
                code=code,
                message=(
                    DATABASE_BUSY_MESSAGE
                    if code == "database_busy"
                    else "could not read local handoffs"
                ),
                exit_code=EXIT_TOOL_ERROR,
            )
    else:
        try:
            with closing(connect_initialized(target)) as connection:
                begin_initialized_write(connection, target)
                result = withdraw_handoff(
                    connection,
                    target.project,
                    getattr(context.args, "handoff_id", ""),
                    reason=getattr(context.args, "reason", ""),
                )
                connection.commit()
            data = {
                "handoff": result.handoff,
                "changed_fields": result.changed_fields,
            }
        except (TaskValidationError, HandoffError) as exc:
            return handoff_failure_result(
                context,
                project_id=project_id,
                code=exc.code,
                message=exc.message,
                exit_code=(
                    EXIT_TOOL_ERROR if exc.code == "internal_error" else EXIT_USAGE
                ),
            )
        except StorageError as exc:
            return handoff_failure_result(
                context,
                project_id=project_id,
                code=exc.code,
                message=exc.message,
                exit_code=EXIT_TOOL_ERROR,
            )
        except sqlite3.Error as exc:
            mapped = operational_sqlite_error(
                exc,
                fallback_message="could not withdraw local handoff",
            )
            return handoff_failure_result(
                context,
                project_id=project_id,
                code=mapped.code,
                message=mapped.message,
                exit_code=EXIT_TOOL_ERROR,
            )

    return CommandResult(
        ok=True,
        command=context.command,
        project_id=project_id,
        data=data,
        text=handoff_text(context.command, data),
        exit_code=EXIT_SUCCESS,
        mutation_outcome=(
            MutationOutcome(
                state_changed=True,
                viewer_relevant=False,
            )
            if context.command == "handoff.withdraw"
            else None
        ),
    )


def handle_review_prepare(context: CommandContext) -> CommandResult:
    target = resolve_context_target(context)
    project_id = target.project.project_id
    try:
        data = prepare_review_packet(
            target,
            getattr(context.args, "task_id", ""),
            initial_connection=context.read_connection_override,
        )
        text = format_review_packet_text(data)
        result = CommandResult(
            ok=True,
            command=context.command,
            project_id=project_id,
            data=data,
            text=text,
            exit_code=EXIT_SUCCESS,
        )
        text_size = len((text + "\n").encode("utf-8"))
        json_size = serialized_json_size(result, data)
        if (
            (context.json_output and json_size > REVIEW_PACKET_MAX_BYTES)
            or (not context.json_output and text_size > REVIEW_PACKET_MAX_BYTES)
        ):
            raise ReviewPacketError(
                "review_packet_too_large",
                OVERSIZED_PACKET_MESSAGE,
            )
        return result
    except (
        ReviewPacketError,
        TaskRepositoryError,
        TaskValidationError,
    ) as exc:
        return review_failure_result(
            context,
            project_id=project_id,
            code=exc.code,
            message=exc.message,
            exit_code=(
                EXIT_TOOL_ERROR
                if exc.code == "internal_error"
                else EXIT_USAGE
            ),
        )
    except StorageError as exc:
        return review_failure_result(
            context,
            project_id=project_id,
            code=exc.code,
            message=exc.message,
            exit_code=EXIT_TOOL_ERROR,
        )
    except sqlite3.Error as exc:
        mapped = operational_sqlite_error(
            exc,
            fallback_message="could not prepare review context",
        )
        return review_failure_result(
            context,
            project_id=project_id,
            code=mapped.code,
            message=mapped.message,
            exit_code=EXIT_TOOL_ERROR,
        )


def review_empty_data(command: str) -> dict[str, Any]:
    if command == "review.prepare":
        return {}
    if command == "review.target.set":
        return {"task": None, "changed_fields": [], "event": None}
    if command == "review.receipt.add":
        return {"receipt": None, "event": None}
    return {"finding": None, "event": None}


def review_failure_result(
    context: CommandContext,
    *,
    project_id: str,
    code: str,
    message: str,
    exit_code: int,
) -> CommandResult:
    return CommandResult(
        ok=False,
        command=context.command,
        project_id=project_id,
        data=review_empty_data(context.command),
        errors=[{"code": code, "message": message}],
        exit_code=exit_code,
    )


def review_text(command: str, data: dict[str, Any]) -> str:
    event = data["event"]
    if command == "review.target.set":
        task = data["task"]
        base = (
            f"\nBase: {task['review_target_base_revision']}"
            if task["review_target_kind"] == "git_snapshot"
            else ""
        )
        return (
            f"Review target set: {task['task_id']}\n"
            f"Target: {task['review_target_kind']} generation "
            f"{task['review_target_generation']}{base}\n"
            f"Event: {event['event_type']} - {event['summary']}"
        )
    if command == "review.receipt.add":
        receipt = data["receipt"]
        return (
            f"Review receipt recorded: {receipt['review_receipt_id']}\n"
            f"Verdict: {receipt['verdict']}  Kind: {receipt['receipt_kind']}\n"
            f"Event: {event['event_type']} - {event['summary']}"
        )
    finding = data["finding"]
    verb = "resolved" if command.endswith("resolve") else "recorded"
    return (
        f"Review finding {verb}: {finding['review_finding_id']}\n"
        f"Severity: {finding['severity']}  Status: {finding['status']}\n"
        f"Event: {event['event_type']} - {event['summary']}"
    )


def handle_review_command(context: CommandContext) -> CommandResult:
    target = resolve_context_target(context)
    project_id = target.project.project_id
    if context.read_only:
        return review_failure_result(
            context,
            project_id=project_id,
            code="invalid_argument",
            message=f"{context.command.replace('.', ' ')} cannot run with --read-only because it writes the database",
            exit_code=EXIT_USAGE,
        )

    try:
        with closing(connect_initialized(target)) as connection:
            with connection:
                if context.command == "review.target.set":
                    result = set_requested_review_target(
                        connection,
                        target.project,
                        getattr(context.args, "task_id", ""),
                        kind=getattr(context.args, "kind", ""),
                        revision=getattr(context.args, "revision", None),
                        database_target=target,
                    )
                    data = {
                        "task": result.task,
                        "changed_fields": result.changed_fields,
                        "event": result.event,
                    }
                elif context.command == "review.receipt.add":
                    result = add_review_receipt(
                        connection,
                        target.project,
                        getattr(context.args, "task_id", ""),
                        reviewer=getattr(context.args, "reviewer", ""),
                        kind=getattr(context.args, "kind", ""),
                        verdict=getattr(context.args, "verdict", ""),
                        summary=getattr(context.args, "summary", ""),
                        user_approved=bool(getattr(context.args, "user_approved", False)),
                        database_target=target,
                    )
                    data = {"receipt": result.receipt, "event": result.event}
                elif context.command == "review.finding.add":
                    result = add_review_finding(
                        connection,
                        target.project,
                        getattr(context.args, "task_id", ""),
                        receipt_id=getattr(context.args, "receipt_id", ""),
                        severity=getattr(context.args, "severity", ""),
                        summary=getattr(context.args, "summary", ""),
                        database_target=target,
                    )
                    data = {"finding": result.finding, "event": result.event}
                else:
                    result = resolve_review_finding(
                        connection,
                        target.project,
                        getattr(context.args, "finding_id", ""),
                        resolution=getattr(context.args, "resolution", ""),
                        database_target=target,
                    )
                    data = {"finding": result.finding, "event": result.event}
    except (TaskValidationError, ReviewEvidenceError) as exc:
        return review_failure_result(
            context,
            project_id=project_id,
            code=exc.code,
            message=exc.message,
            exit_code=EXIT_USAGE,
        )
    except TaskRepositoryError as exc:
        return review_failure_result(
            context,
            project_id=project_id,
            code=exc.code,
            message=exc.message,
            exit_code=EXIT_TOOL_ERROR if exc.code == "internal_error" else EXIT_USAGE,
        )
    except StorageError as exc:
        return review_failure_result(
            context,
            project_id=project_id,
            code=exc.code,
            message=exc.message,
            exit_code=EXIT_TOOL_ERROR,
        )
    except sqlite3.Error as exc:
        mapped = operational_sqlite_error(
            exc,
            fallback_message="could not record structured review evidence",
        )
        return review_failure_result(
            context,
            project_id=project_id,
            code=mapped.code,
            message=mapped.message,
            exit_code=EXIT_TOOL_ERROR,
        )

    return CommandResult(
        ok=True,
        command=context.command,
        project_id=project_id,
        data=data,
        text=review_text(context.command, data),
        exit_code=EXIT_SUCCESS,
        mutation_outcome=MutationOutcome(
            state_changed=True,
            viewer_relevant=True,
        ),
    )


def verification_receipt_empty_data() -> dict[str, Any]:
    return {"receipt": None}


def verification_receipt_failure_result(
    context: CommandContext,
    *,
    project_id: str,
    code: str,
    message: str,
    exit_code: int,
) -> CommandResult:
    return CommandResult(
        ok=False,
        command=context.command,
        project_id=project_id,
        data=verification_receipt_empty_data(),
        errors=[{"code": code, "message": message}],
        exit_code=exit_code,
    )


def verification_receipt_text(receipt: dict[str, Any]) -> str:
    source_revision = receipt["source_revision"]
    return (
        "Verification receipt recorded: "
        f"{receipt['verification_receipt_id']}\n"
        f"Result: {receipt['result']}  Coverage: {receipt['scope_coverage']}\n"
        f"Source: {source_revision['kind']}/generation "
        f"{source_revision['generation']}"
    )


def handle_verification_receipt_add(context: CommandContext) -> CommandResult:
    target = resolve_context_target(context)
    project_id = target.project.project_id
    if context.read_only:
        return verification_receipt_failure_result(
            context,
            project_id=project_id,
            code="invalid_argument",
            message=(
                "verification receipt add cannot run with --read-only because "
                "it writes the database"
            ),
            exit_code=EXIT_USAGE,
        )

    try:
        with closing(connect_initialized(target)) as connection:
            with connection:
                result = add_verification_receipt(
                    connection,
                    target.project,
                    getattr(context.args, "task_id", ""),
                    command_label=getattr(context.args, "command_label", ""),
                    result=getattr(context.args, "result", ""),
                    duration_ms=getattr(context.args, "duration_ms", ""),
                    scope_coverage=getattr(context.args, "scope_coverage", ""),
                    expected_target_generation=getattr(
                        context.args,
                        "expected_target_generation",
                        "",
                    ),
                    database_target=target,
                )
    except (TaskValidationError, VerificationReceiptError) as exc:
        invalid_stored_receipt = (
            isinstance(exc, VerificationReceiptError)
            and exc.code == "invalid_verification_evidence"
            and exc.field is None
        )
        return verification_receipt_failure_result(
            context,
            project_id=project_id,
            code=exc.code,
            message=exc.message,
            exit_code=(
                EXIT_TOOL_ERROR
                if exc.code == "internal_error" or invalid_stored_receipt
                else EXIT_USAGE
            ),
        )
    except TaskRepositoryError as exc:
        return verification_receipt_failure_result(
            context,
            project_id=project_id,
            code=exc.code,
            message=exc.message,
            exit_code=(
                EXIT_TOOL_ERROR if exc.code == "internal_error" else EXIT_USAGE
            ),
        )
    except StorageError as exc:
        return verification_receipt_failure_result(
            context,
            project_id=project_id,
            code=exc.code,
            message=exc.message,
            exit_code=EXIT_TOOL_ERROR,
        )
    except sqlite3.Error as exc:
        mapped = operational_sqlite_error(
            exc,
            fallback_message="could not record verification evidence",
        )
        return verification_receipt_failure_result(
            context,
            project_id=project_id,
            code=mapped.code,
            message=mapped.message,
            exit_code=EXIT_TOOL_ERROR,
        )

    receipt = result.receipt
    return CommandResult(
        ok=True,
        command=context.command,
        project_id=project_id,
        data={"receipt": receipt},
        text=verification_receipt_text(receipt),
        exit_code=EXIT_SUCCESS,
        mutation_outcome=MutationOutcome(
            state_changed=True,
            viewer_relevant=False,
        ),
    )


def bounded_json_limit_from_args(args: argparse.Namespace) -> int | None:
    if not bool(getattr(args, "json", False)):
        return None
    if (
        getattr(args, "command", None) == "review"
        and getattr(args, "review_entity", None) == "prepare"
    ):
        return REVIEW_PACKET_MAX_BYTES
    leaf = getattr(args, "task_command", None)
    if getattr(args, "command", None) != "task":
        return None
    if leaf == "current" and bool(getattr(args, "compact", False)):
        return COMPACT_CURRENT_MAX_BYTES
    if leaf == "next" and bool(getattr(args, "compact", False)):
        return COMPACT_NEXT_MAX_BYTES
    if leaf == "complete" and bool(getattr(args, "check", False)):
        return COMPLETION_CHECK_MAX_BYTES
    return None


def lexical_json_requested(argv: Sequence[str]) -> bool:
    for token in argv:
        if token == "--":
            return False
        if (
            len("--j") <= len(token) <= len("--json")
            and "--json".startswith(token)
        ):
            return True
    return False


def lexical_removed_db_option(argv: Sequence[str]) -> bool:
    return any(token == "--db" or token.startswith("--db=") for token in argv)


def apply_post_commit_maintenance(
    context: CommandContext,
    result: CommandResult,
) -> CommandResult:
    outcome = result.mutation_outcome
    if not result.ok or outcome is None or not outcome.state_changed:
        return result
    try:
        if result.maintenance_target is None:
            raise RuntimeError("post-commit target basis is unavailable")
        warnings = run_post_commit_maintenance(
            result.maintenance_target,
            outcome,
        )
    except Exception:
        warnings = []
        if outcome.viewer_relevant:
            warnings.append(
                {
                    "code": "viewer_refresh_failed",
                    "message": VIEWER_WARNING_MESSAGES["failed"],
                }
            )
        warnings.append(
            {
                "code": "backup_failed",
                "message": BACKUP_WARNING_MESSAGES["failed"],
            }
        )
    if not warnings:
        return result
    warning_text = "\n".join(warning["message"] for warning in warnings)
    return replace(
        result,
        warnings=[*result.warnings, *warnings],
        text=(
            f"{result.text}\n{warning_text}"
            if result.text
            else warning_text
        ),
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    _target_override: DatabaseTarget | None = None,
    _maintenance_enabled: bool = True,
) -> int:
    raw_argv = tuple(argv if argv is not None else sys.argv[1:])
    parser = build_parser()
    try:
        if lexical_removed_db_option(raw_argv):
            raise CommandLineError(
                "invalid_option",
                "option is not available",
                exit_code=EXIT_TOOL_ERROR,
            )
        args = parser.parse_args(raw_argv)
        if args.command is None:
            parser.print_help()
            return EXIT_SUCCESS
        if (
            args.command == "setup"
            and bool(getattr(args, "read_only", False))
            and getattr(args, "confirm_relocation", None) is not None
        ):
            raise CommandLineError(
                "invalid_option_combination",
                "--confirm-relocation cannot be used with --read-only",
                exit_code=EXIT_USAGE,
            )
        if args.command == "task" and args.task_command is None:
            raise CommandLineError(
                "invalid_argument",
                "task requires a subcommand: add, list, next, current, effort, show, checkpoint, edit, or complete",
            )
        if (
            args.command == "task"
            and getattr(args, "task_command", None) in {"current", "next"}
            and bool(getattr(args, "compact", False))
            and not bool(getattr(args, "json", False))
        ):
            raise CommandLineError(
                "invalid_option_combination",
                "--compact requires --json",
                exit_code=EXIT_TOOL_ERROR,
            )
        if args.command == "handoff" and args.handoff_command is None:
            raise CommandLineError(
                "invalid_argument",
                "handoff requires a subcommand: record, list, show, or withdraw",
            )
        if args.command == "review" and (
            getattr(args, "review_entity", None) is None
            or (
                getattr(args, "review_entity", None) != "prepare"
                and getattr(args, "review_action", None) is None
            )
        ):
            raise CommandLineError(
                "invalid_argument",
                "review requires prepare, target set, receipt add, finding add, or finding resolve",
            )
        if args.command == "verification" and (
            getattr(args, "verification_entity", None) is None
            or getattr(args, "verification_action", None) is None
        ):
            raise CommandLineError(
                "invalid_argument",
                "verification requires receipt add",
            )
        if (
            args.command == "review"
            and getattr(args, "review_entity", None) == "target"
            and getattr(args, "review_action", None) == "set"
            and str(getattr(args, "kind", "")).strip() != "git_snapshot"
            and getattr(args, "revision", None) is None
        ):
            raise CommandLineError(
                "invalid_argument",
                "review target set requires --revision unless --kind is git_snapshot",
            )
        context = make_context(args, target_override=_target_override)
        result = handle_command(context)
        if _maintenance_enabled:
            result = apply_post_commit_maintenance(context, result)
        return emit_result(
            result,
            json_output=context.json_output,
            max_json_bytes=bounded_json_limit_from_args(args),
        )
    except CommandLineError as exc:
        json_output = lexical_json_requested(raw_argv)
        if not json_output and exc.code in {"invalid_command", "invalid_option"}:
            print(f"taskgov: {exc.message}", file=sys.stderr)
            return exc.exit_code
        result = error_result("parse", exc.code, exc.message, exc.exit_code)
        return emit_result(
            result,
            json_output=json_output,
            max_json_bytes=(
                COMPLETION_CHECK_MAX_BYTES if json_output else None
            ),
        )
