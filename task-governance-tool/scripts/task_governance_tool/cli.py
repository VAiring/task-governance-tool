"""Command-line interface for task-governance-tool."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from task_governance_tool import __version__

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
    add_common_options(task_subparsers.add_parser("add", help="register an explicit task"))
    add_common_options(task_subparsers.add_parser("list", help="list compact task slices"))
    add_common_options(task_subparsers.add_parser("next", help="show next actionable tasks"))
    add_common_options(task_subparsers.add_parser("show", help="show one task and recent events"))
    add_common_options(task_subparsers.add_parser("edit", help="update task state or metadata"))

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
    )


def handle_command(context: CommandContext) -> CommandResult:
    return error_result(
        context.command,
        "internal_error",
        f"{context.command}: handler not implemented yet",
        EXIT_TOOL_ERROR,
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
