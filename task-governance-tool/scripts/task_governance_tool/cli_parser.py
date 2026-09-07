"""Public CLI parser construction and sanitized parse errors."""

from __future__ import annotations

import argparse
from typing import Any

from task_governance_tool import __version__
from task_governance_tool.tasks import TASK_VERIFICATION_INPUT_LIMIT


EXIT_USAGE = 1
EXIT_TOOL_ERROR = 2


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
    task_add_parser.add_argument(
        "--verification",
        default="",
        help=(
            "verification expectation "
            f"({TASK_VERIFICATION_INPUT_LIMIT:,} characters or fewer)"
        ),
    )
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
    task_edit_parser.add_argument(
        "--verification",
        default=argparse.SUPPRESS,
        help=(
            "verification expectation "
            f"({TASK_VERIFICATION_INPUT_LIMIT:,} characters or fewer)"
        ),
    )
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
    task_edit_parser.add_argument(
        "--runner-plan-action",
        metavar="{replace,rebind,detach,disable}",
        default=argparse.SUPPRESS,
        help=(
            "replace, rebind, or detach the selected Task's Runner Plan "
            "entry; disable sets global trusted_local=false while preserving "
            "entries; the first replace on an absent Plan opts the repository "
            "in with trusted_local=true; replace reads one JSON draft from "
            "stdin"
        ),
    )
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
    review_receipt_add_parser.add_argument("--reviewer-class", default=None)
    review_receipt_add_parser.add_argument("--model-state", default=None)
    review_receipt_add_parser.add_argument("--declared-model-id", default=None)
    review_receipt_add_parser.add_argument("--skill-state", default=None)
    review_receipt_add_parser.add_argument("--declared-skill-id", default=None)
    review_receipt_add_parser.add_argument("--declared-skill-version", default=None)
    review_receipt_add_parser.add_argument(
        "--review-profile",
        dest="review_profiles",
        action="append",
        default=None,
    )
    review_receipt_add_parser.add_argument(
        "--review-lens",
        dest="review_lenses",
        action="append",
        default=None,
    )
    review_receipt_add_parser.add_argument("--context-relation", default=None)
    review_receipt_add_parser.add_argument(
        "--review-method",
        dest="review_methods",
        action="append",
        default=None,
    )

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
    verification_receipt_add_parser.add_argument("--result", required=True)
    verification_receipt_add_parser.add_argument("--duration-ms", required=True)
    verification_receipt_add_parser.add_argument("--scope-coverage", required=True)
    verification_receipt_add_parser.add_argument(
        "--expected-target-generation",
        required=True,
    )

    return parser
