"""Command-line interface for task-governance-tool."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections.abc import Sequence
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from task_governance_tool import __version__
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
from task_governance_tool.storage import (
    DATABASE_BUSY_MESSAGE,
    StatusResult,
    StorageError,
    begin_initialized_write,
    connect_initialized,
    connect_initialized_readonly,
    connect_snapshot_readonly,
    initialize_database,
    inspect_database,
    is_sqlite_busy_or_locked,
    operational_sqlite_error,
    resolve_database_target,
    skill_root_from_script,
)
from task_governance_tool.selection import select_next_tasks
from task_governance_tool.reviews import (
    ReviewEvidenceError,
    add_review_finding,
    add_review_receipt,
    resolve_review_finding,
    set_requested_review_target,
)
from task_governance_tool.self_status import inspect_local_package
from task_governance_tool.tasks import (
    CURRENT_STATUSES,
    TaskRepositoryError,
    TaskValidationError,
    add_task,
    edit_task,
    list_current_tasks,
    list_tasks,
    show_task,
    validate_current_status_filter,
    validate_task_id,
)
from task_governance_tool.viewer import (
    SNAPSHOT_VERSION,
    ViewerError,
    build_viewer_snapshot,
    render_viewer_html,
    resolve_viewer_output_target,
    write_viewer_html,
)

EXIT_SUCCESS = 0
EXIT_USAGE = 1
EXIT_TOOL_ERROR = 2


@dataclass(frozen=True)
class CommandContext:
    command: str
    repo: str
    db: str | None
    json_output: bool
    read_only: bool
    args: argparse.Namespace


@dataclass(frozen=True)
class CommandResult:
    ok: bool
    command: str
    data: dict[str, Any] = field(default_factory=dict)
    project_id: str | None = None
    db_path: str | None = None
    warnings: list[dict[str, str]] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)
    text: str = ""
    exit_code: int = EXIT_SUCCESS

    def to_json_object(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "command": self.command,
            "project_id": self.project_id,
            "db_path": self.db_path,
            "data": self.data,
            "warnings": self.warnings,
            "errors": self.errors,
        }


class TaskgovArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise CommandLineError("invalid_argument", message)


class CommandLineError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def emit_result(result: CommandResult, *, json_output: bool) -> int:
    if json_output:
        print(json.dumps(result.to_json_object(), indent=2, sort_keys=True))
    elif result.text:
        print(result.text)
    elif result.errors:
        print(result.errors[0]["message"], file=sys.stderr)
    return result.exit_code


def error_result(command: str, code: str, message: str, exit_code: int) -> CommandResult:
    return CommandResult(
        ok=False,
        command=command,
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
    parser.add_argument("--db", default=argparse.SUPPRESS, help="explicit SQLite database path")
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
        description="Local SQLite-backed task state helper for Codex.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    add_common_options(parser)

    subparsers = parser.add_subparsers(dest="command")

    db_parser = subparsers.add_parser("db", help="database commands")
    db_subparsers = db_parser.add_subparsers(dest="db_command")
    add_common_options(db_subparsers.add_parser("init", help="create or migrate the task database"))
    add_common_options(db_subparsers.add_parser("status", help="inspect database status without mutation"))

    self_parser = subparsers.add_parser("self", help="installed package inspection commands")
    self_subparsers = self_parser.add_subparsers(dest="self_command")
    self_status_parser = self_subparsers.add_parser(
        "status",
        help="compare packaged core files with the release manifest",
    )
    add_common_options(self_status_parser)

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
    task_current_parser = task_subparsers.add_parser("current", help="rediscover active or held work")
    add_common_options(task_current_parser)
    task_current_parser.add_argument("--status", default=None)
    task_current_parser.add_argument("--limit", default=None)
    task_effort_parser = task_subparsers.add_parser(
        "effort",
        help="show an optional informational effort observation",
    )
    add_common_options(task_effort_parser)
    task_effort_parser.add_argument("task_id")
    task_show_parser = task_subparsers.add_parser("show", help="show one task and recent events")
    add_common_options(task_show_parser)
    task_show_parser.add_argument("task_id")
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

    review_parser = subparsers.add_parser("review", help="structured review evidence commands")
    review_subparsers = review_parser.add_subparsers(dest="review_entity")

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
    review_receipt_add_parser.add_argument("--reviewer", required=True)
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

    web_parser = subparsers.add_parser("web", help="static viewer commands")
    web_subparsers = web_parser.add_subparsers(dest="web_command")
    web_export_parser = web_subparsers.add_parser(
        "export",
        help="render a self-contained offline task viewer",
        description="Render a self-contained offline task viewer.",
    )
    add_common_options(web_export_parser)
    web_export_parser.add_argument("--output", default=None, help="explicit .html or .htm output path")

    return parser


def command_name(args: argparse.Namespace) -> str:
    if args.command == "db" and args.db_command:
        return f"db.{args.db_command}"
    if args.command == "self" and args.self_command:
        return f"self.{args.self_command}"
    if args.command == "task" and args.task_command:
        return f"task.{args.task_command}"
    if args.command == "handoff" and args.handoff_command:
        return f"handoff.{args.handoff_command}"
    if args.command == "review" and args.review_entity and args.review_action:
        return f"review.{args.review_entity}.{args.review_action}"
    if args.command == "web" and args.web_command:
        return f"web.{args.web_command}"
    if args.command:
        return args.command
    return "help"


def make_context(args: argparse.Namespace) -> CommandContext:
    return CommandContext(
        command=command_name(args),
        repo=getattr(args, "repo", "."),
        db=getattr(args, "db", None),
        json_output=bool(getattr(args, "json", False)),
        read_only=bool(getattr(args, "read_only", False)),
        args=args,
    )


def handle_command(context: CommandContext) -> CommandResult:
    if context.command == "db.init":
        return handle_db_init(context)
    if context.command == "db.status":
        return handle_db_status(context)
    if context.command == "self.status":
        return handle_self_status(context)
    if context.command == "task.add":
        return handle_task_add(context)
    if context.command == "task.list":
        return handle_task_list(context)
    if context.command == "task.next":
        return handle_task_next(context)
    if context.command == "task.current":
        return handle_task_current(context)
    if context.command == "task.effort":
        return handle_task_effort(context)
    if context.command == "task.show":
        return handle_task_show(context)
    if context.command == "task.edit":
        return handle_task_edit(context)
    if context.command.startswith("handoff."):
        return handle_handoff_command(context)
    if context.command.startswith("review."):
        return handle_review_command(context)
    if context.command == "web.export":
        return handle_web_export(context)
    return error_result(
        context.command,
        "internal_error",
        f"{context.command}: handler not implemented yet",
        EXIT_TOOL_ERROR,
    )


def cli_script_path() -> Path:
    return Path(__file__).resolve().parents[1] / "taskgov.py"


def self_status_text(data: dict[str, Any]) -> str:
    lines = [
        f"Package: {data['package_name']} {data['package_version']}",
        f"Release origin: {data['release_origin'] or 'unknown'}",
        f"Manifest version: {data['manifest_version'] if data['manifest_version'] is not None else 'unknown'}",
        f"Status: {data['status']}",
        f"Changed core files: {data['changed_core_count']}",
    ]
    if data["changed_core_paths"]:
        lines.append("Changed paths:")
        lines.extend(f"- {path}" for path in data["changed_core_paths"])
        if data["changed_core_paths_truncated"]:
            lines.append("- ...")
    if data["unknown_reasons"]:
        lines.append(f"Unknown reasons: {', '.join(data['unknown_reasons'])}")
    lines.append(f"Suggested action: {data['suggested_action']}")
    return "\n".join(lines)


def handle_self_status(context: CommandContext) -> CommandResult:
    package_status = inspect_local_package(
        skill_root_from_script(cli_script_path()),
        installed_version=__version__,
    )
    data = package_status.to_data()
    warnings: list[dict[str, str]] = []
    if package_status.status == "modified":
        warnings.append(
            {
                "code": "package_core_modified",
                "message": (
                    f"{package_status.changed_core_count} packaged core file(s) "
                    "differ from the release manifest; continue current task"
                ),
            }
        )
    elif package_status.status == "unknown":
        warnings.append(
            {
                "code": "package_status_unknown",
                "message": "package self-status is unknown; continue current task",
            }
        )
    return CommandResult(
        ok=True,
        command=context.command,
        data=data,
        warnings=warnings,
        text=self_status_text(data),
        exit_code=EXIT_SUCCESS,
    )


def web_export_empty_data(output_path: str | None) -> dict[str, Any]:
    return {
        "output_path": output_path,
        "written": False,
        "replaced": False,
        "task_count": 0,
        "event_count": 0,
        "generated_at": None,
        "snapshot_version": SNAPSHOT_VERSION,
    }


def web_export_failure_result(
    context: CommandContext,
    *,
    project_id: str,
    db_path: str,
    output_path: str | None,
    code: str,
    message: str,
    exit_code: int,
) -> CommandResult:
    return CommandResult(
        ok=False,
        command=context.command,
        project_id=project_id,
        db_path=db_path,
        data=web_export_empty_data(output_path),
        errors=[{"code": code, "message": message}],
        exit_code=exit_code,
    )


def web_export_text(data: dict[str, Any]) -> str:
    action = "written" if data["written"] else "previewed"
    return "\n".join(
        [
            f"Viewer {action}: {data['output_path']}",
            f"Tasks: {data['task_count']}  Events: {data['event_count']}",
            f"Generated: {data['generated_at']}",
        ]
    )


def viewer_error_exit_code(code: str) -> int:
    if code in {"output_path_invalid", "output_parent_missing"}:
        return EXIT_USAGE
    return EXIT_TOOL_ERROR


def handle_web_export(context: CommandContext) -> CommandResult:
    script_path = cli_script_path()
    target = resolve_database_target(repo=context.repo, db=context.db, script_path=script_path)
    project_id = target.project.project_id
    db_path = str(target.db_path)
    try:
        output_target = resolve_viewer_output_target(
            output=getattr(context.args, "output", None),
            skill_root=skill_root_from_script(script_path),
            database_target=target,
        )
    except ViewerError as exc:
        return web_export_failure_result(
            context,
            project_id=project_id,
            db_path=db_path,
            output_path=None,
            code=exc.code,
            message=exc.message,
            exit_code=viewer_error_exit_code(exc.code),
        )

    output_path = str(output_target.path)
    try:
        with closing(connect_snapshot_readonly(target.db_path)) as connection:
            snapshot_result = build_viewer_snapshot(connection, target)
            rendered = render_viewer_html(snapshot_result.snapshot)
    except StorageError as exc:
        return web_export_failure_result(
            context,
            project_id=project_id,
            db_path=db_path,
            output_path=output_path,
            code=exc.code,
            message=exc.message,
            exit_code=EXIT_TOOL_ERROR,
        )
    except ViewerError as exc:
        return web_export_failure_result(
            context,
            project_id=project_id,
            db_path=db_path,
            output_path=output_path,
            code=exc.code,
            message=exc.message,
            exit_code=EXIT_TOOL_ERROR,
        )
    except sqlite3.Error:
        return web_export_failure_result(
            context,
            project_id=project_id,
            db_path=db_path,
            output_path=output_path,
            code="internal_error",
            message="could not read viewer snapshot",
            exit_code=EXIT_TOOL_ERROR,
        )
    except Exception:
        return web_export_failure_result(
            context,
            project_id=project_id,
            db_path=db_path,
            output_path=output_path,
            code="internal_error",
            message="viewer snapshot could not be rendered",
            exit_code=EXIT_TOOL_ERROR,
        )

    replaced = False
    written = False
    if not context.read_only:
        try:
            replaced = write_viewer_html(output_target, rendered)
            written = True
        except ViewerError as exc:
            return web_export_failure_result(
                context,
                project_id=project_id,
                db_path=db_path,
                output_path=output_path,
                code=exc.code,
                message=exc.message,
                exit_code=viewer_error_exit_code(exc.code),
            )

    data = {
        "output_path": output_path,
        "written": written,
        "replaced": replaced,
        "task_count": snapshot_result.task_count,
        "event_count": snapshot_result.event_count,
        "generated_at": snapshot_result.snapshot["generated_at"],
        "snapshot_version": SNAPSHOT_VERSION,
    }
    return CommandResult(
        ok=True,
        command=context.command,
        project_id=project_id,
        db_path=db_path,
        data=data,
        text=web_export_text(data),
        exit_code=EXIT_SUCCESS,
    )


def handle_db_init(context: CommandContext) -> CommandResult:
    target = resolve_database_target(repo=context.repo, db=context.db, script_path=cli_script_path())
    if context.read_only:
        return CommandResult(
            ok=False,
            command=context.command,
            project_id=target.project.project_id,
            db_path=str(target.db_path),
            errors=[
                {
                    "code": "invalid_argument",
                    "message": "db init cannot run with --read-only because it writes the database",
                }
            ],
            exit_code=EXIT_USAGE,
        )

    try:
        result = initialize_database(target)
    except StorageError as exc:
        return CommandResult(
            ok=False,
            command=context.command,
            project_id=target.project.project_id,
            db_path=str(target.db_path),
            errors=[{"code": exc.code, "message": exc.message}],
            exit_code=EXIT_TOOL_ERROR,
        )

    data = {
        "created": result.created,
        "migrations_applied": result.migrations_applied,
        "schema_version": result.schema_version,
    }
    action = "created" if result.created else "initialized"
    return CommandResult(
        ok=True,
        command=context.command,
        project_id=target.project.project_id,
        db_path=str(target.db_path),
        data=data,
        warnings=result.warnings,
        text=(
            f"DB {action}: {target.db_path}\n"
            f"Project: {target.project.project_id}\n"
            f"Schema version: {result.schema_version}\n"
            f"Migrations applied: {', '.join(str(v) for v in result.migrations_applied) or 'none'}"
        ),
        exit_code=EXIT_SUCCESS,
    )


def status_data(
    status: StatusResult,
    effort_profile: EffortProfile | None = None,
) -> dict[str, Any]:
    data = {
        "exists": status.exists,
        "needs_init": status.needs_init,
        "needs_migration": status.needs_migration,
        "schema_version": status.schema_version,
        "counts": status.counts,
        "handoff_delivery": {
            "adapter_enabled": False,
            "sync_due": False,
        },
    }
    if effort_profile is not None and effort_profile.enabled:
        data["effort_advisory"] = {
            "enabled": True,
            "profile": effort_profile.profile_id,
            "profile_hash": effort_profile.profile_hash,
        }
    elif (
        effort_profile is not None
        and effort_profile.present
        and not effort_profile.valid
    ):
        data["effort_advisory"] = {
            "enabled": False,
            "configuration": "invalid",
        }
    return data


def status_text(
    status: StatusResult,
    effort_profile: EffortProfile | None = None,
) -> str:
    schema_version = status.schema_version if status.schema_version is not None else "none"
    if status.needs_init:
        state = "needs init"
    elif status.needs_migration:
        state = "needs migration"
    elif status.error_code:
        state = status.error_code.replace("_", " ")
    else:
        state = "ready"
    counts = status.counts
    text = (
        f"DB: {status.target.db_path}\n"
        f"Project: {status.target.project.project_id}\n"
        f"Status: {state}\n"
        f"Schema version: {schema_version}\n"
        f"Active: {counts['active']}  Paused: {counts['paused']}  "
        f"Blocked: {counts['blocked']}  "
        f"Review pending: {counts['review_pending']}  Done: {counts['done']}\n"
        f"Next actionable: {counts['next_actionable']}\n"
        f"Pending handoffs: {counts['handoff_pending']}  "
        "Adapter enabled: false  Sync due: false"
    )
    if effort_profile is not None and effort_profile.enabled:
        text += f"\nEffort advisory: enabled ({effort_profile.profile_id})"
    elif (
        effort_profile is not None
        and effort_profile.present
        and not effort_profile.valid
    ):
        text += "\nEffort advisory: disabled (invalid configuration)"
    return text


def handle_db_status(context: CommandContext) -> CommandResult:
    target = resolve_database_target(repo=context.repo, db=context.db, script_path=cli_script_path())
    status = inspect_database(target)
    effort_profile = load_effort_profile(skill_root_from_script(cli_script_path()))
    effort_warnings = []
    if effort_profile.present and not effort_profile.valid:
        effort_warnings.append(
            {
                "code": "effort_advisory_profile_invalid",
                "message": "Effort Advisory configuration is invalid; advisory remains disabled.",
                "suggested_action": "continue",
            }
        )
    if status.error_code:
        return CommandResult(
            ok=False,
            command=context.command,
            project_id=target.project.project_id,
            db_path=str(target.db_path),
            data=status_data(status, effort_profile),
            warnings=effort_warnings,
            errors=[{"code": status.error_code, "message": status.error_message or status.error_code}],
            text=status_text(status, effort_profile),
            exit_code=EXIT_TOOL_ERROR,
        )

    return CommandResult(
        ok=True,
        command=context.command,
        project_id=target.project.project_id,
        db_path=str(target.db_path),
        data=status_data(status, effort_profile),
        warnings=effort_warnings,
        text=status_text(status, effort_profile),
        exit_code=EXIT_SUCCESS,
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
    db_path: str | None,
    exc: TaskValidationError | TaskRepositoryError,
) -> CommandResult:
    exit_code = EXIT_TOOL_ERROR if exc.code == "internal_error" else EXIT_USAGE
    return CommandResult(
        ok=False,
        command=context.command,
        project_id=project_id,
        db_path=db_path,
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
    target = resolve_database_target(repo=context.repo, db=context.db, script_path=cli_script_path())
    if context.read_only:
        return CommandResult(
            ok=False,
            command=context.command,
            project_id=target.project.project_id,
            db_path=str(target.db_path),
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
            db_path=str(target.db_path),
            exc=exc,
        )
    except TaskRepositoryError as exc:
        return validation_failure_result(
            context,
            project_id=target.project.project_id,
            db_path=str(target.db_path),
            exc=exc,
        )
    except StorageError as exc:
        return CommandResult(
            ok=False,
            command=context.command,
            project_id=target.project.project_id,
            db_path=str(target.db_path),
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
            db_path=str(target.db_path),
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
        db_path=str(target.db_path),
        data=data,
        text=task_add_text(result.task, result.event, result.contract_write),
        exit_code=EXIT_SUCCESS,
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
    target = resolve_database_target(repo=context.repo, db=context.db, script_path=cli_script_path())
    try:
        with closing(connect_initialized_readonly(target)) as connection:
            result = list_tasks(connection, target.project, **task_list_input(context.args))
    except TaskValidationError as exc:
        return validation_failure_result(
            context,
            project_id=target.project.project_id,
            db_path=str(target.db_path),
            exc=exc,
        )
    except StorageError as exc:
        return CommandResult(
            ok=False,
            command=context.command,
            project_id=target.project.project_id,
            db_path=str(target.db_path),
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
            db_path=str(target.db_path),
            data={"tasks": [], "count": 0, "limit": 0},
            errors=[{"code": code, "message": message}],
            exit_code=EXIT_TOOL_ERROR,
        )

    data = {"tasks": result.tasks, "count": result.count, "limit": result.limit}
    return CommandResult(
        ok=True,
        command=context.command,
        project_id=target.project.project_id,
        db_path=str(target.db_path),
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
    db_path: str | None,
    code: str,
    message: str,
    exit_code: int,
) -> CommandResult:
    return CommandResult(
        ok=False,
        command=context.command,
        project_id=project_id,
        db_path=db_path,
        data=task_next_empty_data(),
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
    target = resolve_database_target(repo=context.repo, db=context.db, script_path=cli_script_path())
    status = inspect_database(target)
    if status.error_code:
        return task_next_failure_result(
            context,
            project_id=target.project.project_id,
            db_path=str(target.db_path),
            code=status.error_code,
            message=status.error_message or status.error_code,
            exit_code=EXIT_TOOL_ERROR,
        )

    try:
        with closing(connect_initialized_readonly(target)) as connection:
            result = select_next_tasks(connection, target.project, **task_next_input(context.args))
    except TaskValidationError as exc:
        return task_next_failure_result(
            context,
            project_id=target.project.project_id,
            db_path=str(target.db_path),
            code=exc.code,
            message=exc.message,
            exit_code=EXIT_USAGE,
        )
    except StorageError as exc:
        return task_next_failure_result(
            context,
            project_id=target.project.project_id,
            db_path=str(target.db_path),
            code=exc.code,
            message=exc.message,
            exit_code=EXIT_TOOL_ERROR,
        )
    except sqlite3.Error as exc:
        code = "database_busy" if _is_transient_sqlite_lock(exc) else "internal_error"
        return task_next_failure_result(
            context,
            project_id=target.project.project_id,
            db_path=str(target.db_path),
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
    paused_count = int(status.counts["paused"])
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
    return CommandResult(
        ok=True,
        command=context.command,
        project_id=target.project.project_id,
        db_path=str(target.db_path),
        data=data,
        warnings=warnings,
        text=task_next_text(result.tasks, result.count, result.limit, warnings),
        exit_code=EXIT_SUCCESS,
    )


def task_current_empty_data(
    statuses: Sequence[str] = CURRENT_STATUSES,
) -> dict[str, Any]:
    return {
        "tasks": [],
        "count": 0,
        "limit": 0,
        "statuses": list(statuses),
    }


def task_current_text(tasks: list[dict[str, Any]], count: int, limit: int) -> str:
    lines = [f"Current tasks: {count} (limit {limit})"]
    for task in tasks:
        lines.append(
            f"{task['task_id']} [{task['status']}] {task['priority']} - "
            f"{task['title']} | {task['suggested_next_action']}"
        )
    return "\n".join(lines)


def handle_task_current(context: CommandContext) -> CommandResult:
    target = resolve_database_target(repo=context.repo, db=context.db, script_path=cli_script_path())
    try:
        status_filter = validate_current_status_filter(
            getattr(context.args, "status", None)
        )
    except TaskValidationError as exc:
        return CommandResult(
            ok=False,
            command=context.command,
            project_id=target.project.project_id,
            db_path=str(target.db_path),
            data=task_current_empty_data(),
            errors=[{"code": exc.code, "message": exc.message}],
            exit_code=EXIT_USAGE,
        )
    effective_statuses = (
        CURRENT_STATUSES if status_filter is None else (status_filter,)
    )
    try:
        with closing(connect_initialized_readonly(target)) as connection:
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
            db_path=str(target.db_path),
            data=task_current_empty_data(effective_statuses),
            errors=[{"code": exc.code, "message": exc.message}],
            exit_code=EXIT_USAGE,
        )
    except StorageError as exc:
        return CommandResult(
            ok=False,
            command=context.command,
            project_id=target.project.project_id,
            db_path=str(target.db_path),
            data=task_current_empty_data(effective_statuses),
            errors=[{"code": exc.code, "message": exc.message}],
            exit_code=EXIT_TOOL_ERROR,
        )
    except sqlite3.Error as exc:
        code = "database_busy" if _is_transient_sqlite_lock(exc) else "internal_error"
        return CommandResult(
            ok=False,
            command=context.command,
            project_id=target.project.project_id,
            db_path=str(target.db_path),
            data=task_current_empty_data(effective_statuses),
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
    return CommandResult(
        ok=True,
        command=context.command,
        project_id=target.project.project_id,
        db_path=str(target.db_path),
        data=data,
        text=task_current_text(result.tasks, result.count, result.limit),
        exit_code=EXIT_SUCCESS,
    )


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
    target = resolve_database_target(repo=context.repo, db=context.db, script_path=cli_script_path())
    raw_task_id = getattr(context.args, "task_id", "")
    try:
        task_id = validate_task_id(raw_task_id)
        profile = load_effort_profile(skill_root_from_script(cli_script_path()))
        with closing(connect_initialized_readonly(target)) as connection:
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
            db_path=str(target.db_path),
            exc=exc,
        )
    except EffortAdvisoryError as exc:
        return CommandResult(
            ok=False,
            command=context.command,
            project_id=target.project.project_id,
            db_path=str(target.db_path),
            data=task_effort_empty_data(task_id),
            errors=[{"code": exc.code, "message": exc.message}],
            exit_code=EXIT_USAGE if exc.code == "not_found" else EXIT_TOOL_ERROR,
        )
    except StorageError as exc:
        return CommandResult(
            ok=False,
            command=context.command,
            project_id=target.project.project_id,
            db_path=str(target.db_path),
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
            db_path=str(target.db_path),
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
        db_path=str(target.db_path),
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
    lines.append(f"Suggested next action: {suggested_next_action}")
    if events:
        latest = events[0]
        lines.append(f"Latest event: {latest['event_type']} - {latest['summary']}")
    return "\n".join(lines)


def handle_task_show(context: CommandContext) -> CommandResult:
    target = resolve_database_target(repo=context.repo, db=context.db, script_path=cli_script_path())
    try:
        with closing(connect_initialized_readonly(target)) as connection:
            result = show_task(connection, target.project, getattr(context.args, "task_id", ""))
    except TaskValidationError as exc:
        return validation_failure_result(
            context,
            project_id=target.project.project_id,
            db_path=str(target.db_path),
            exc=exc,
        )
    except TaskRepositoryError as exc:
        if exc.code == "not_found":
            return CommandResult(
                ok=False,
                command=context.command,
                project_id=target.project.project_id,
                db_path=str(target.db_path),
                data={
                    "task": None,
                    "events": [],
                    "suggested_next_action": "",
                    "review_evidence": None,
                    "handoff_summary": None,
                    "contract": None,
                },
                errors=[{"code": exc.code, "message": exc.message}],
                exit_code=EXIT_USAGE,
            )
        return validation_failure_result(
            context,
            project_id=target.project.project_id,
            db_path=str(target.db_path),
            exc=exc,
        )
    except HandoffError:
        return CommandResult(
            ok=False,
            command=context.command,
            project_id=target.project.project_id,
            db_path=str(target.db_path),
            data={
                "task": None,
                "events": [],
                "suggested_next_action": "",
                "review_evidence": None,
                "handoff_summary": None,
                "contract": None,
            },
            errors=[{"code": "internal_error", "message": "could not show task"}],
            exit_code=EXIT_TOOL_ERROR,
        )
    except StorageError as exc:
        return CommandResult(
            ok=False,
            command=context.command,
            project_id=target.project.project_id,
            db_path=str(target.db_path),
            data={
                "task": None,
                "events": [],
                "suggested_next_action": "",
                "review_evidence": None,
                "handoff_summary": None,
                "contract": None,
            },
            errors=[{"code": exc.code, "message": exc.message}],
            exit_code=EXIT_TOOL_ERROR,
        )
    except sqlite3.Error as exc:
        code = "database_busy" if _is_transient_sqlite_lock(exc) else "internal_error"
        return CommandResult(
            ok=False,
            command=context.command,
            project_id=target.project.project_id,
            db_path=str(target.db_path),
            data={
                "task": None,
                "events": [],
                "suggested_next_action": "",
                "review_evidence": None,
                "handoff_summary": None,
                "contract": None,
            },
            errors=[
                {
                    "code": code,
                    "message": (
                        DATABASE_BUSY_MESSAGE
                        if code == "database_busy"
                        else "could not show task"
                    ),
                }
            ],
            exit_code=EXIT_TOOL_ERROR,
        )

    data = {
        "task": result.task,
        "events": result.events,
        "suggested_next_action": result.suggested_next_action,
        "review_evidence": result.review_evidence,
        "handoff_summary": result.handoff_summary,
        "contract": result.contract,
    }
    return CommandResult(
        ok=True,
        command=context.command,
        project_id=target.project.project_id,
        db_path=str(target.db_path),
        data=data,
        text=task_show_text(
            result.task,
            result.events,
            result.suggested_next_action,
            result.review_evidence,
            result.handoff_summary,
            result.contract,
        ),
        exit_code=EXIT_SUCCESS,
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
    db_path: str | None,
    code: str,
    message: str,
    exit_code: int,
) -> CommandResult:
    return CommandResult(
        ok=False,
        command=context.command,
        project_id=project_id,
        db_path=db_path,
        data=task_edit_empty_data(),
        errors=[{"code": code, "message": message}],
        exit_code=exit_code,
    )


def task_edit_text(
    task: dict[str, Any],
    changed_fields: list[str],
    event: dict[str, Any] | None,
    contract_write: dict[str, Any] | None = None,
) -> str:
    changed = ", ".join(changed_fields) if changed_fields else "none"
    lines = [
        f"Task updated: {task['task_id']}",
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
    target = resolve_database_target(repo=context.repo, db=context.db, script_path=cli_script_path())
    if context.read_only:
        return task_edit_failure_result(
            context,
            project_id=target.project.project_id,
            db_path=str(target.db_path),
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
            db_path=str(target.db_path),
            code=exc.code,
            message=exc.message,
            exit_code=EXIT_USAGE,
        )
    except TaskRepositoryError as exc:
        exit_code = EXIT_TOOL_ERROR if exc.code == "internal_error" else EXIT_USAGE
        return task_edit_failure_result(
            context,
            project_id=target.project.project_id,
            db_path=str(target.db_path),
            code=exc.code,
            message=exc.message,
            exit_code=exit_code,
        )
    except StorageError as exc:
        return task_edit_failure_result(
            context,
            project_id=target.project.project_id,
            db_path=str(target.db_path),
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
            db_path=str(target.db_path),
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
        db_path=str(target.db_path),
        data=data,
        text=task_edit_text(
            result.task,
            result.changed_fields,
            result.event,
            result.contract_write,
        ),
        exit_code=EXIT_SUCCESS,
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
    db_path: str,
    code: str,
    message: str,
    exit_code: int,
) -> CommandResult:
    return CommandResult(
        ok=False,
        command=context.command,
        project_id=project_id,
        db_path=db_path,
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
    db_path: str,
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
                db_path=db_path,
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
                db_path=db_path,
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
                db_path=db_path,
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
            db_path=db_path,
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
        db_path=db_path,
        data=data,
        text=handoff_text(context.command, data),
        exit_code=EXIT_SUCCESS,
    )


def handle_handoff_command(context: CommandContext) -> CommandResult:
    target = resolve_database_target(
        repo=context.repo,
        db=context.db,
        script_path=cli_script_path(),
    )
    project_id = target.project.project_id
    db_path = str(target.db_path)
    if context.command in {"handoff.record", "handoff.withdraw"} and context.read_only:
        return handoff_failure_result(
            context,
            project_id=project_id,
            db_path=db_path,
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
            db_path=db_path,
        )

    if context.command in {"handoff.list", "handoff.show"}:
        try:
            with closing(connect_initialized_readonly(target)) as connection:
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
                db_path=db_path,
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
                db_path=db_path,
                code=exc.code,
                message=exc.message,
                exit_code=EXIT_TOOL_ERROR,
            )
        except sqlite3.Error as exc:
            code = "database_busy" if _is_transient_sqlite_lock(exc) else "internal_error"
            return handoff_failure_result(
                context,
                project_id=project_id,
                db_path=db_path,
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
                db_path=db_path,
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
                db_path=db_path,
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
                db_path=db_path,
                code=mapped.code,
                message=mapped.message,
                exit_code=EXIT_TOOL_ERROR,
            )

    return CommandResult(
        ok=True,
        command=context.command,
        project_id=project_id,
        db_path=db_path,
        data=data,
        text=handoff_text(context.command, data),
        exit_code=EXIT_SUCCESS,
    )


def review_empty_data(command: str) -> dict[str, Any]:
    if command == "review.target.set":
        return {"task": None, "changed_fields": [], "event": None}
    if command == "review.receipt.add":
        return {"receipt": None, "event": None}
    return {"finding": None, "event": None}


def review_failure_result(
    context: CommandContext,
    *,
    project_id: str,
    db_path: str,
    code: str,
    message: str,
    exit_code: int,
) -> CommandResult:
    return CommandResult(
        ok=False,
        command=context.command,
        project_id=project_id,
        db_path=db_path,
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
    target = resolve_database_target(repo=context.repo, db=context.db, script_path=cli_script_path())
    project_id = target.project.project_id
    db_path = str(target.db_path)
    if context.read_only:
        return review_failure_result(
            context,
            project_id=project_id,
            db_path=db_path,
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
            db_path=db_path,
            code=exc.code,
            message=exc.message,
            exit_code=EXIT_USAGE,
        )
    except TaskRepositoryError as exc:
        return review_failure_result(
            context,
            project_id=project_id,
            db_path=db_path,
            code=exc.code,
            message=exc.message,
            exit_code=EXIT_TOOL_ERROR if exc.code == "internal_error" else EXIT_USAGE,
        )
    except StorageError as exc:
        return review_failure_result(
            context,
            project_id=project_id,
            db_path=db_path,
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
            db_path=db_path,
            code=mapped.code,
            message=mapped.message,
            exit_code=EXIT_TOOL_ERROR,
        )

    return CommandResult(
        ok=True,
        command=context.command,
        project_id=project_id,
        db_path=db_path,
        data=data,
        text=review_text(context.command, data),
        exit_code=EXIT_SUCCESS,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        if args.command is None:
            parser.print_help()
            return EXIT_SUCCESS
        if args.command == "db" and args.db_command is None:
            raise CommandLineError("invalid_argument", "db requires a subcommand: init or status")
        if args.command == "self" and args.self_command is None:
            raise CommandLineError("invalid_argument", "self requires a subcommand: status")
        if args.command == "task" and args.task_command is None:
            raise CommandLineError(
                "invalid_argument",
                "task requires a subcommand: add, list, next, show, or edit",
            )
        if args.command == "handoff" and args.handoff_command is None:
            raise CommandLineError(
                "invalid_argument",
                "handoff requires a subcommand: record, list, show, or withdraw",
            )
        if args.command == "review" and (
            getattr(args, "review_entity", None) is None
            or getattr(args, "review_action", None) is None
        ):
            raise CommandLineError(
                "invalid_argument",
                "review requires target set, receipt add, finding add, or finding resolve",
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
        if args.command == "web" and args.web_command is None:
            raise CommandLineError("invalid_argument", "web requires a subcommand: export")
        context = make_context(args)
        result = handle_command(context)
        return emit_result(result, json_output=context.json_output)
    except CommandLineError as exc:
        json_output = "--json" in (argv or sys.argv[1:])
        result = error_result("parse", exc.code, exc.message, EXIT_USAGE)
        return emit_result(result, json_output=json_output)
