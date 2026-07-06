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
from task_governance_tool.storage import (
    StatusResult,
    StorageError,
    connect,
    connect_readonly,
    initialize_database,
    inspect_database,
    resolve_database_target,
)
from task_governance_tool.tasks import (
    TaskRepositoryError,
    TaskValidationError,
    add_task,
    edit_task,
    list_tasks,
    show_task,
    validate_task_edit_input,
    validate_task_input,
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
    task_list_parser = task_subparsers.add_parser("list", help="list compact task slices")
    add_common_options(task_list_parser)
    task_list_parser.add_argument("--status", default=None)
    task_list_parser.add_argument("--kind", default=None)
    task_list_parser.add_argument("--lane", default=None)
    task_list_parser.add_argument("--priority", default=None)
    task_list_parser.add_argument("--tag", default=None)
    task_list_parser.add_argument("--limit", default=None)
    task_list_parser.add_argument("--include-done", action="store_true", default=False)
    add_common_options(task_subparsers.add_parser("next", help="show next actionable tasks"))
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
    task_edit_parser.add_argument("--review-tier", default=argparse.SUPPRESS)
    task_edit_parser.add_argument("--verification", default=argparse.SUPPRESS)
    task_edit_parser.add_argument("--tags", default=argparse.SUPPRESS)
    task_edit_parser.add_argument("--add-note", default=argparse.SUPPRESS)

    return parser


def command_name(args: argparse.Namespace) -> str:
    if args.command == "db" and args.db_command:
        return f"db.{args.db_command}"
    if args.command == "task" and args.task_command:
        return f"task.{args.task_command}"
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
    if context.command == "task.add":
        return handle_task_add(context)
    if context.command == "task.list":
        return handle_task_list(context)
    if context.command == "task.show":
        return handle_task_show(context)
    if context.command == "task.edit":
        return handle_task_edit(context)
    return error_result(
        context.command,
        "internal_error",
        f"{context.command}: handler not implemented yet",
        EXIT_TOOL_ERROR,
    )


def cli_script_path() -> Path:
    return Path(__file__).resolve().parents[1] / "taskgov.py"


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
        text=(
            f"DB {action}: {target.db_path}\n"
            f"Project: {target.project.project_id}\n"
            f"Schema version: {result.schema_version}\n"
            f"Migrations applied: {', '.join(str(v) for v in result.migrations_applied) or 'none'}"
        ),
        exit_code=EXIT_SUCCESS,
    )


def status_data(status: StatusResult) -> dict[str, Any]:
    return {
        "exists": status.exists,
        "needs_init": status.needs_init,
        "needs_migration": status.needs_migration,
        "schema_version": status.schema_version,
        "counts": status.counts,
    }


def status_text(status: StatusResult) -> str:
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
    return (
        f"DB: {status.target.db_path}\n"
        f"Project: {status.target.project.project_id}\n"
        f"Status: {state}\n"
        f"Schema version: {schema_version}\n"
        f"Active: {counts['active']}  Blocked: {counts['blocked']}  "
        f"Review pending: {counts['review_pending']}  Done: {counts['done']}\n"
        f"Next actionable: {counts['next_actionable']}"
    )


def handle_db_status(context: CommandContext) -> CommandResult:
    target = resolve_database_target(repo=context.repo, db=context.db, script_path=cli_script_path())
    status = inspect_database(target)
    if status.error_code:
        return CommandResult(
            ok=False,
            command=context.command,
            project_id=target.project.project_id,
            db_path=str(target.db_path),
            data=status_data(status),
            errors=[{"code": status.error_code, "message": status.error_message or status.error_code}],
            text=status_text(status),
            exit_code=EXIT_TOOL_ERROR,
        )

    return CommandResult(
        ok=True,
        command=context.command,
        project_id=target.project.project_id,
        db_path=str(target.db_path),
        data=status_data(status),
        text=status_text(status),
        exit_code=EXIT_SUCCESS,
    )


def task_add_input(args: argparse.Namespace) -> dict[str, Any]:
    return {
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


def task_add_text(task: dict[str, Any], event: dict[str, Any]) -> str:
    lines = [
        f"Task added: {task['task_id']}",
        f"Title: {task['title']}",
        f"Kind: {task['kind']}  Status: {task['status']}  Priority: {task['priority']}",
        f"Review tier: {task['review_tier']}",
        f"Event: {event['event_type']}",
    ]
    if task["kind"] == "sequential":
        lines.insert(3, f"Lane: {task['lane']}  Order: {task['lane_order']}")
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
    try:
        validate_task_input(**task_input)
    except TaskValidationError as exc:
        return validation_failure_result(
            context,
            project_id=target.project.project_id,
            db_path=str(target.db_path),
            exc=exc,
        )

    try:
        initialize_database(target)
        with closing(connect(target.db_path)) as connection:
            with connection:
                result = add_task(connection, target.project, **task_input)
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
        return CommandResult(
            ok=False,
            command=context.command,
            project_id=target.project.project_id,
            db_path=str(target.db_path),
            errors=[{"code": "internal_error", "message": "could not add task"}],
            exit_code=EXIT_TOOL_ERROR,
        )

    data = {"task": result.task, "event": result.event}
    return CommandResult(
        ok=True,
        command=context.command,
        project_id=target.project.project_id,
        db_path=str(target.db_path),
        data=data,
        text=task_add_text(result.task, result.event),
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
    status = inspect_database(target)
    if status.error_code:
        return CommandResult(
            ok=False,
            command=context.command,
            project_id=target.project.project_id,
            db_path=str(target.db_path),
            data={"tasks": [], "count": 0, "limit": 0},
            errors=[{"code": status.error_code, "message": status.error_message or status.error_code}],
            exit_code=EXIT_TOOL_ERROR,
        )

    try:
        with closing(connect_readonly(target.db_path)) as connection:
            result = list_tasks(connection, target.project, **task_list_input(context.args))
    except TaskValidationError as exc:
        return validation_failure_result(
            context,
            project_id=target.project.project_id,
            db_path=str(target.db_path),
            exc=exc,
        )
    except sqlite3.Error as exc:
        return CommandResult(
            ok=False,
            command=context.command,
            project_id=target.project.project_id,
            db_path=str(target.db_path),
            errors=[{"code": "internal_error", "message": "could not list tasks"}],
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


def task_show_text(task: dict[str, Any], events: list[dict[str, Any]], suggested_next_action: str) -> str:
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
    if task["verification"]:
        lines.append(f"Verification: {task['verification']}")
    if task["blocked_reason"]:
        lines.append(f"Blocked: {task['blocked_reason']}")
    lines.append(f"Suggested next action: {suggested_next_action}")
    if events:
        latest = events[0]
        lines.append(f"Latest event: {latest['event_type']} - {latest['summary']}")
    return "\n".join(lines)


def handle_task_show(context: CommandContext) -> CommandResult:
    target = resolve_database_target(repo=context.repo, db=context.db, script_path=cli_script_path())
    status = inspect_database(target)
    if status.error_code:
        return CommandResult(
            ok=False,
            command=context.command,
            project_id=target.project.project_id,
            db_path=str(target.db_path),
            data={"task": None, "events": [], "suggested_next_action": ""},
            errors=[{"code": status.error_code, "message": status.error_message or status.error_code}],
            exit_code=EXIT_TOOL_ERROR,
        )

    try:
        with closing(connect_readonly(target.db_path)) as connection:
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
                data={"task": None, "events": [], "suggested_next_action": ""},
                errors=[{"code": exc.code, "message": exc.message}],
                exit_code=EXIT_USAGE,
            )
        return validation_failure_result(
            context,
            project_id=target.project.project_id,
            db_path=str(target.db_path),
            exc=exc,
        )
    except sqlite3.Error as exc:
        return CommandResult(
            ok=False,
            command=context.command,
            project_id=target.project.project_id,
            db_path=str(target.db_path),
            errors=[{"code": "internal_error", "message": "could not show task"}],
            exit_code=EXIT_TOOL_ERROR,
        )

    data = {
        "task": result.task,
        "events": result.events,
        "suggested_next_action": result.suggested_next_action,
    }
    return CommandResult(
        ok=True,
        command=context.command,
        project_id=target.project.project_id,
        db_path=str(target.db_path),
        data=data,
        text=task_show_text(result.task, result.events, result.suggested_next_action),
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
    "review_tier",
    "verification",
    "tags",
    "add_note",
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


def task_edit_text(task: dict[str, Any], changed_fields: list[str], event: dict[str, Any]) -> str:
    changed = ", ".join(changed_fields) if changed_fields else "none"
    return "\n".join(
        [
            f"Task updated: {task['task_id']}",
            f"Title: {task['title']}",
            f"Status: {task['status']}  Priority: {task['priority']}  Kind: {task['kind']}",
            f"Changed: {changed}",
            f"Event: {event['event_type']}",
        ]
    )


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
    try:
        validate_task_edit_input(**edit_input)
    except TaskValidationError as exc:
        return task_edit_failure_result(
            context,
            project_id=target.project.project_id,
            db_path=str(target.db_path),
            code=exc.code,
            message=exc.message,
            exit_code=EXIT_USAGE,
        )

    if not target.db_path.exists():
        return task_edit_failure_result(
            context,
            project_id=target.project.project_id,
            db_path=str(target.db_path),
            code="db_not_initialized",
            message="database is not initialized; run db init first",
            exit_code=EXIT_TOOL_ERROR,
        )

    try:
        initialize_database(target)
        with closing(connect(target.db_path)) as connection:
            with connection:
                result = edit_task(
                    connection,
                    target.project,
                    getattr(context.args, "task_id", ""),
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
    except sqlite3.Error:
        return task_edit_failure_result(
            context,
            project_id=target.project.project_id,
            db_path=str(target.db_path),
            code="internal_error",
            message="could not edit task",
            exit_code=EXIT_TOOL_ERROR,
        )

    data = {"task": result.task, "changed_fields": result.changed_fields, "event": result.event}
    return CommandResult(
        ok=True,
        command=context.command,
        project_id=target.project.project_id,
        db_path=str(target.db_path),
        data=data,
        text=task_edit_text(result.task, result.changed_fields, result.event),
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
        if args.command == "task" and args.task_command is None:
            raise CommandLineError(
                "invalid_argument",
                "task requires a subcommand: add, list, next, show, or edit",
            )
        context = make_context(args)
        result = handle_command(context)
        return emit_result(result, json_output=context.json_output)
    except CommandLineError as exc:
        json_output = "--json" in (argv or sys.argv[1:])
        result = error_result("parse", exc.code, exc.message, EXIT_USAGE)
        return emit_result(result, json_output=json_output)
