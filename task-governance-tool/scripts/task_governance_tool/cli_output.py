"""Shared CLI results, bounded JSON fitting, and JSON/text stream emission.

Command handlers and post-commit maintenance remain in cli. Internal mutation
and maintenance metadata travels with a result but is never serialized.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field, replace
from typing import Any

from task_governance_tool.cli_parser import EXIT_TOOL_ERROR
from task_governance_tool.compact import CompactProjectionError
from task_governance_tool.maintenance import MutationOutcome
from task_governance_tool.storage import DatabaseTarget


EXIT_SUCCESS = 0
BOUNDED_DIAGNOSTIC_OMISSION_MESSAGE = (
    "diagnostic details omitted to satisfy the bounded output limit"
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
        rendered = json.dumps(
            result.to_json_object(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=result.command != "review.prepare",
        )
        if hasattr(sys.stdout, "buffer"):
            sys.stdout.buffer.write((rendered + "\n").encode("utf-8"))
        else:
            print(rendered)
    elif result.text:
        if result.command == "review.prepare" and hasattr(sys.stdout, "buffer"):
            sys.stdout.buffer.write((result.text + "\n").encode("utf-8"))
        else:
            print(result.text)
    elif result.errors:
        print(result.errors[0]["message"], file=sys.stderr)
    return result.exit_code


def serialized_json_size(result: CommandResult, data: dict[str, Any]) -> int:
    """Keep the legacy pretty/ASCII/CRLF budget for selection and omission.

    Wire compaction must not silently admit more rows or diagnostic content.
    This compatibility budget also bounds the shorter UTF-8 wire encoding.
    """
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
