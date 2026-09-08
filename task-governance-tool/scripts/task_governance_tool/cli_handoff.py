"""Handoff command handling within the shared CLI orchestration."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from typing import TYPE_CHECKING, Any

from task_governance_tool.cli_output import EXIT_SUCCESS, CommandResult
from task_governance_tool.cli_parser import EXIT_TOOL_ERROR, EXIT_USAGE
from task_governance_tool.cli_text import handoff_text
from task_governance_tool.handoffs import (
    HandoffError,
    list_handoffs,
    record_handoff,
    show_handoff,
    withdraw_handoff,
)
from task_governance_tool.maintenance import MutationOutcome
from task_governance_tool.storage import (
    DATABASE_BUSY_MESSAGE,
    StorageError,
    begin_initialized_write,
    connect_initialized,
    operational_sqlite_error,
)
from task_governance_tool.tasks import TaskRepositoryError
from task_governance_tool.task_values import TaskValidationError

if TYPE_CHECKING:
    from task_governance_tool.cli import CommandContext


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


def _handle_handoff_record(
    context: CommandContext,
    *,
    target: Any,
    project_id: str,
) -> CommandResult:
    from task_governance_tool.cli import _is_transient_sqlite_lock

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
    from task_governance_tool.cli import (
        _is_transient_sqlite_lock,
        context_read_connection,
        resolve_context_target,
    )

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
