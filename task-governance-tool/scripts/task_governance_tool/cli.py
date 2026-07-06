"""Command-line interface for task-governance-tool."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from task_governance_tool import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="taskgov",
        description="Local SQLite-backed task state helper for Codex.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--repo", default=".", help="target project root; defaults to current directory")
    parser.add_argument("--db", help="explicit SQLite database path")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument(
        "--read-only",
        action="store_true",
        help="prohibit database creation, migration, or writes",
    )

    subparsers = parser.add_subparsers(dest="command")

    db_parser = subparsers.add_parser("db", help="database commands")
    db_subparsers = db_parser.add_subparsers(dest="db_command")
    db_subparsers.add_parser("init", help="create or migrate the task database")
    db_subparsers.add_parser("status", help="inspect database status without mutation")

    task_parser = subparsers.add_parser("task", help="task commands")
    task_subparsers = task_parser.add_subparsers(dest="task_command")
    task_subparsers.add_parser("add", help="register an explicit task")
    task_subparsers.add_parser("list", help="list compact task slices")
    task_subparsers.add_parser("next", help="show next actionable tasks")
    task_subparsers.add_parser("show", help="show one task and recent events")
    task_subparsers.add_parser("edit", help="update task state or metadata")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    parser.error("command handlers are not implemented yet")
    return 2
