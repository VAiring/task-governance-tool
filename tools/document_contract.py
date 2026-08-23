"""Offline semantic checks for this repository's documentation authority."""
from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path


sys.dont_write_bytecode = True

DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[1]
AUTHORITY = "docs/authority.md"
DESIGN = "docs/design.md"
HISTORY_INDEX = "docs/history/README.md"
RELEASE_INSTALL = "docs/release-install.md"
EXECUTION_INDEX = "docs/execution-contracts/README.md"
M22 = "docs/execution-contracts/tg-m22-evidence-ledger.md"
M23 = "docs/execution-contracts/tg-m23-derived-evidence.md"
M23_PROCESS = "docs/execution-contracts/tg-m23-process-safety.md"
M24 = "docs/execution-contracts/tg-m24-verification-runner.md"

CANONICAL_DOCS = (
    "AGENTS.md",
    "README.md",
    AUTHORITY,
    "docs/specification.md",
    DESIGN,
    "plan.md",
    EXECUTION_INDEX,
    M22,
    M23,
    M23_PROCESS,
    M24,
    HISTORY_INDEX,
)
METRIC_DOCS = CANONICAL_DOCS + (RELEASE_INSTALL,)

# These sections are closed authority edges. Their prose and link order are not
# part of the contract; the required destination set is.
ROUTE_SECTIONS = (
    (AUTHORITY, "## Mandatory Start Set", ("../AGENTS.md",)),
    (
        AUTHORITY,
        "## Selective Current Authority",
        ("specification.md", "design.md", "../plan.md"),
    ),
    (
        AUTHORITY,
        "## Mixed Current And Conditional Execution Authority",
        (
            "execution-contracts/tg-m22-evidence-ledger.md#tg-m22-sequence",
            "execution-contracts/tg-m23-derived-evidence.md#tg-m23-derived-evidence",
            "execution-contracts/tg-m23-process-safety.md#tg-m23-process-safety",
            "execution-contracts/tg-m24-verification-runner.md#tg-m24-verification-runner",
        ),
    ),
    (
        AUTHORITY,
        "## Documentation Governance Sequence",
        ("../plan.md#tg-doc-sequence",),
    ),
    (AUTHORITY, "## Non-Authoritative History", ("history/README.md",)),
    (
        EXECUTION_INDEX,
        "## Indexed Contracts",
        (
            "tg-m22-evidence-ledger.md#tg-m22-sequence",
            "tg-m23-derived-evidence.md#tg-m23-derived-evidence",
            "tg-m23-process-safety.md#tg-m23-process-safety",
            "tg-m24-verification-runner.md#tg-m24-verification-runner",
        ),
    ),
    (
        EXECUTION_INDEX,
        "## Cross-Sequence Documentation Gateway",
        (
            "../../plan.md#tg-doc-sequence",
            "../../plan.md#tg-doc-2",
            "../../plan.md#tg-doc-3",
        ),
    ),
    (
        M23,
        "## Process Safety Route",
        ("tg-m23-process-safety.md#tg-m23-process-safety",),
    ),
    (
        M23_PROCESS,
        "## Parent Route",
        ("tg-m23-derived-evidence.md#tg-m23-1",),
    ),
)

SOURCE_OWNER_RELATIONS = (
    ("agents.md", ("agent behavior", "safety", "workflow")),
    ("docs/specification.md", ("product behavior",)),
    ("docs/design.md", ("implementation structure",)),
    ("plan.md", ("current decisions", "open issues")),
    ("docs/authority.md", ("task contract", "indexed execution detail")),
)
SOURCE_KNOWN_OWNERS = tuple(owner for owner, _topics in SOURCE_OWNER_RELATIONS)
SOURCE_START_RELATIONS = (
    (
        ("start of every task", "start of each task"),
        ("read and follow", "read"),
        ("minimal start set",),
    ),
    (("agents.md",),),
    (("docs/authority.md",),),
    (("live task contract",), ("public cli",)),
)
REREAD_RELATIONS = (
    (
        ("re-read", "reread"),
        ("minimal start set",),
        ("new task",),
        ("milestone",),
        ("execution-unit boundary", "execution unit boundary"),
        ("planning",),
        ("editing",),
        ("verification",),
        ("review",),
    ),
    (
        (
            "implementation-affecting decision",
            "implementation affecting decision",
            "decision that affects implementation",
            "decision affecting implementation",
        ),
        ("docs/authority.md",),
        ("task contract",),
        ("directly coupled",),
        ("implementation",),
        ("tests",),
    ),
    (
        ("full read",),
        ("docs/specification.md",),
        ("docs/design.md",),
        ("plan.md",),
        ("conditional contract",),
        ("authority-layout", "authority layout"),
        ("transition",),
        ("cross-cutting", "cross cutting"),
        ("missing",),
        ("ambiguous",),
        ("conflict",),
    ),
)
TRIGGER_ROUTE_RELATIONS = (
    (("supported product behavior",), ("docs/specification.md",)),
    (("module ownership", "test architecture"), ("docs/design.md",)),
    (("current decision", "open issue"), ("plan.md",)),
    (
        ("tg-m22 unit purpose",),
        ("docs/execution-contracts/tg-m22-evidence-ledger.md#tg-m22-sequence",),
    ),
    (
        ("tg-m23 unit", "core data"),
        ("docs/execution-contracts/tg-m23-derived-evidence.md",),
    ),
    (
        ("tg-m23 windows process", "atomic publication"),
        (
            "docs/execution-contracts/tg-m23-process-safety.md",
            "core owner/router",
        ),
    ),
    (
        ("documentation governance", "tg-doc unit"),
        ("../plan.md#tg-doc-sequence", "plan.md#tg-doc-2", "plan.md#tg-doc-3"),
    ),
    (
        ("tg-m24 unit detail",),
        (
            "accepted predecessor",
            "current unit",
            "inactive unit",
            "superseded unit",
            "current execution contract",
            "ascii anchor",
        ),
    ),
    (("published artifact", "release identity"), ("docs/release-install.md",)),
    (
        ("live status", "completion history"),
        ("public cli", "live task contract", "no git-document mirror"),
    ),
    (
        ("historical lineage", "retired evidence"),
        ("docs/history/readme.md", "exceptional reason"),
    ),
)
ROWS_M22 = (
    ("TG-M22.1A / 25", "tg_task_0e1d93d81eb843ab", "accepted TG-M21.4D and completed TG-DOC.1"),
    ("TG-M22.2 / 30", "tg_task_88bfe19eb6cffe2e", "accepted TG-M22.1A"),
    ("TG-M21.5 / 40", "tg_task_e7701fb907020905", "accepted TG-M22.2"),
    ("TG-M22.3 / 50", "tg_task_ae6f52c4f7b25549", "accepted TG-M21.5"),
    ("TG-M22.4 / 60", "tg_task_0a90b4caf566a8fd", "accepted TG-M22.3"),
)
ROWS_M23 = (
    ("TG-M23.1 / 10", "tg_task_722ac8a308a23d1c", "accepted TG-M22.4"),
    ("TG-M23.2 / 20", "tg_task_d5511d2ca7db93dc", "accepted TG-M23.1"),
    ("TG-M23.3 / 30", "tg_task_0ada32d2b4f9759d", "accepted TG-M23.2"),
)
ROWS_M24 = (
    (
        "TG-M24.R1 / 10",
        "tg_task_8af2eee60acb0830",
        "reviewed R2 bootstrap boundary",
    ),
    ("TG-M24.R2A / 20", "tg_task_96a03f1d76799f79", "accepted TG-M24.R1"),
    ("TG-M24.R2B / 25", "tg_task_ca8d0d81cd1962ab", "accepted TG-M24.R2A"),
    ("TG-M24.R2C / 30", "tg_task_252701fe03f530af", "accepted TG-M24.R2B"),
    ("TG-M24.R4A / 40", "tg_task_83d2af496ac84982", "accepted TG-M24.R2C"),
    ("TG-M24.R4V / 45", "tg_task_006bee9937e25af9", "accepted TG-M24.R4A"),
    ("TG-M24.R3A / 50", "tg_task_a6d113455aa2cdfe", "accepted TG-M24.R4V"),
    ("TG-M24.R3B / 60", "tg_task_c343ed2ec8acedf8", "accepted TG-M24.R3A"),
    ("TG-M24.R4B / 70", "tg_task_e04fd31e6713cfa1", "accepted TG-M24.R3B"),
    ("TG-M24.R5 / 80", "tg_task_89e9ac8d34df2e95", "accepted TG-M24.R4B"),
    ("TG-M24.2A / 90", "tg_task_2c6fd4707ac1e81b", "accepted TG-M24.R5"),
    ("TG-M24.2B / 100", "tg_task_f8880aeb93c3ad52", "accepted TG-M24.2A"),
    ("TG-M24.2C / 110", "tg_task_8cc06027db5be49f", "accepted TG-M24.2B"),
    ("TG-M24.2D / 120", "tg_task_fafad7bc62df7576", "accepted TG-M24.2C"),
    ("TG-M24.3 / 130", "tg_task_dc015144091f8e60", "accepted TG-M24.2D"),
    ("TG-M24.4A / 140", "tg_task_0da786589eb5144a", "accepted TG-M24.3"),
    ("TG-M24.4B / 150", "tg_task_220ff054e445f40e", "accepted TG-M24.4A"),
    ("TG-M24.4C / 160", "tg_task_b0a3bf776bea1e93", "accepted TG-M24.4B"),
    ("TG-M24.4D / 170", "tg_task_f81f2d126f033a59", "accepted TG-M24.4C"),
    ("TG-M24.CP4 / 180", "tg_task_a9e1229d594594d4", "accepted TG-M24.4D"),
)
ROWS_DOC = (
    (
        "TG-DOC.2 / 40",
        "tg_task_bf2aa245019f5c9f",
        "TG-M23-DERIVED-EVIDENCE",
        "accepted TG-M23.3",
        "accepted predecessor; required before TG-M24.R1",
    ),
    (
        "TG-DOC.3 / 20",
        "tg_task_99371b8db2d43eb2",
        "TG-DOC-LIFECYCLE",
        "accepted TG-M24.CP4 and accepted TG-DOC.2",
        "inactive post-M24",
    ),
)

R2C_BOUNDARY_HEADING = "## Accepted TG-M24.R2C Trusted-Local Runner Architecture Boundary"
R2C_BOUNDARY_TABLE_HEADING = "### Closed Runner-Slice Module Registry"
R2C_TABLE_HEADER_ROLES = {
    "layer": (r"\blayer\b", r"\b(?:id|identifier)\b"),
    "modules": (r"\bmodules?\b",),
    "responsibility": (r"\bresponsibilit", r"\b(?:runner|ownership)\b"),
    "imports": (r"\bimports?\b",),
    "forbidden": (r"\b(?:forbidden|reverse)\b",),
    "route": (r"\b(?:route|owner)\b", r"\b(?:transitional|nonconformance)\b"),
}
R2C_LAYER_MODULES = {
    "cli": ("cli.py",),
    "service": ("verification_runner_service.py",),
    "repository": ("storage.py", "tasks.py", "contracts.py", "reviews.py", "verification_receipts.py", "completion.py", "evidence_ledger.py", "evidence_projection.py", "maintenance.py"),
    "target_plan": ("artifact_manifest.py", "verification_runner_git.py", "verification_runner_plan.py"),
    "value_model": ("verification_runner.py",),
    "runtime_identity": ("verification_runner_runtime.py", "self_status.py"),
    "lifecycle": ("verification_runner_lifecycle.py",),
    "process_adapter": ("verification_runner_process.py",),
    "os_adapter": ("_verification_runner_win32.py",),
}
R2C_LAYER_IMPORTS = {
    "cli": ("service",),
    "service": ("repository", "target_plan", "value_model", "runtime_identity", "lifecycle", "process_adapter"),
    "repository": ("value_model",),
    "target_plan": ("repository", "value_model"),
    "value_model": (),
    "runtime_identity": ("repository", "value_model"),
    "lifecycle": (),
    "process_adapter": ("value_model", "os_adapter"),
    "os_adapter": ("value_model",),
}
R2C_LAYER_ROUTE_UNITS = {
    "cli": ("R4B",),
    "service": ("R4B", "2C"),
    "repository": ("R3A", "R3B", "R4B"),
    "target_plan": ("R4B", "2A"),
    "value_model": ("R4V", "R3A", "R3B", "R4B"),
    "runtime_identity": ("R4B", "2B"),
    "lifecycle": ("R4B", "2B"),
    "process_adapter": ("R4B", "2B"),
    "os_adapter": ("2B",),
}
R2C_LAYER_RESPONSIBILITY_PATTERNS = {
    "cli": (r"public", r"pars", r"format", r"dispatch.*(?:parent )?service"),
    "service": (r"parent.*orchestrat", r"sole.*(?:eligibility|authority)", r"persistence", r"cleanup acceptance"),
    "repository": (r"canonical sqlite", r"task/contract", r"business gate", r"parent service"),
    "target_plan": (r"parent-invoked", r"target", r"plan"),
    "value_model": (r"pure", r"identifier", r"value validation"),
    "runtime_identity": (r"parent-invoked", r"executable", r"package-integrity"),
    "lifecycle": (r"parent-requested", r"private attempt tree", r"(?:removal|absence proof)"),
    "process_adapter": (r"closed request", r"job", r"output", r"process-tree zero", r"close handles", r"closed result"),
    "os_adapter": (r"windows job", r"process", r"stdio", r"handle primitives"),
}
R2C_LAYER_FORBIDDEN_PATTERNS = {
    "cli": (r"no runner eligibility", r"no runner dispatch.*process_adapter.*os_adapter"),
    "service": (r"no os mechanics", r"no delegation.*cleanup acceptance"),
    "repository": (r"no process launch", r"no import.*process_adapter.*os_adapter", r"no filesystem cleanup"),
    "target_plan": (r"no cli policy", r"no .*verification-command launch", r"no .*cleanup acceptance"),
    "value_model": (r"no i/o", r"no import.*cli.*service.*repository", r"business-gate"),
    "runtime_identity": (r"no process launch", r"no .*database", r"no .*cleanup acceptance"),
    "lifecycle": (r"no process start", r"no .*job/stdio/handle", r"no .*final cleanup acceptance"),
    "process_adapter": (r"no canonical state", r"no import.*cli.*service.*repository", r"business gate"),
    "os_adapter": (r"no parent policy", r"repository", r"cleanup acceptance", r"reverse import"),
}
R2C_RECORD_MEMBERS = {
    "RunnerProcessRequestV1": ("version", "attempt_id", "executable", "materialized_root", "scratch_root", "clean_environment", "steps", "cancel_signal"),
    "RunnerProcessStepV1": ("ordinal", "step_id", "mode", "entrypoint", "argv", "cwd", "shell", "path_lookup", "timeout_seconds", "cpu_seconds", "memory_mib", "process_limit", "output_byte_limit"),
    "RunnerProcessResultV1": ("version", "attempt_id", "outcome", "reason", "launch_state", "failed_step_ordinal", "duration_ms", "cpu_time_ms", "peak_job_memory_bytes", "total_process_count", "process_zero", "handles_closed", "raw_output_discarded", "steps"),
    "RunnerProcessStepResultV1": ("ordinal", "outcome", "reason", "launch_state", "cpu_time_ms", "peak_job_memory_bytes", "total_process_count"),
    "RunnerPrivateTreeResultV1": ("attempt_id", "state"),
}
R2C_BOUND_CONTROLS = {
    "request_version": ("=", "1"),
    "accepted_plan_blob_utf8_bytes": ("<=", "65536"),
    "attempt_id": ("=", "ASCII /tg_verification_runner_attempt_[0-9a-f]{16}/ (47 bytes)"),
    "identifier": ("=", "ASCII /[a-z0-9][a-z0-9._-]{0,63}/ (1..64 bytes)"),
    "result_code": ("=", "ASCII /[a-z][a-z0-9_]{0,63}/ (1..64 bytes)"),
    "absolute_path": ("=", 'well-formed Unicode, absolute normalized Windows path, no NUL or Unicode Cc, no "." or ".." segment, 1..4096 UTF-8 bytes and 1..4096 UTF-16 code units'),
    "relative_path": ("=", '"." or 1..32 "/"-separated ASCII /[A-Za-z0-9_][A-Za-z0-9._-]{0,127}/ components, no "." or ".." component, total 1..512 bytes'),
    "script_entrypoint": ("=", 'non-dot relative_path ending in ".py"'),
    "module_entrypoint": ("=", '1..16 "."-separated ASCII /[A-Za-z_][A-Za-z0-9_]{0,63}/ components, total 1..512 bytes'),
    "literal_arg": ("=", "well-formed Unicode with no Unicode Cc, 0..4096 UTF-8 bytes and 0..4096 UTF-16 code units"),
    "path_ownership": ("=", "executable is a parent-verified fixed absolute package-runtime identity outside materialized_root and scratch_root with no PATH lookup; materialized_root and scratch_root are distinct target and scratch children of one owned attempt root; no symlink or reparse traversal"),
    "resolved_relative_path": ("=", "every entrypoint and cwd resolves beneath materialized_root"),
    "step_count": ("=", "1..16"),
    "argv_count_per_step": ("=", "0..64"),
    "timeout_seconds": ("=", "1..900"),
    "total_timeout_seconds": ("=", "1..1800"),
    "cpu_seconds": ("=", "1..900"),
    "memory_mib": ("=", "64..2048"),
    "process_limit": ("=", "1..32"),
    "output_byte_limit": ("=", "1048576"),
    "command_line_utf16_units": ("<=", "24576 after exact Windows quoting and fixed bootstrap insertion"),
    "clean_environment_entry_count": ("=", "11"),
    "clean_environment_value_utf8_bytes": ("=", "1..4096"),
    "clean_environment_keys": ("=", "APPDATA, HOME, LOCALAPPDATA, PYTHONDONTWRITEBYTECODE, PYTHONNOUSERSITE, PYTHONUTF8, SystemRoot, TEMP, TMP, USERPROFILE, WINDIR"),
    "clean_environment_paths": ("=", "APPDATA=scratch_root/roaming; HOME=USERPROFILE=scratch_root/home; LOCALAPPDATA=scratch_root/local; TEMP=TMP=scratch_root/tmp; SystemRoot=WINDIR=parent-verified Windows directory"),
    "clean_environment_literals": ("=", 'PYTHONDONTWRITEBYTECODE=PYTHONNOUSERSITE=PYTHONUTF8="1"'),
    "clean_environment_block_utf16_units": ("<=", "24576 including the terminal double NUL"),
    "result_version": ("=", "1"),
    "result_attempt_id": ("=", "request.attempt_id"),
    "result_outcome": ("=", "result_code"),
    "result_reason": ("=", "null or result_code"),
    "step_result_outcome": ("=", "result_code"),
    "step_result_reason": ("=", "null or result_code"),
    "launch_state": ("=", "no_launch|launched"),
    "private_tree_state": ("=", "absent|uncertain"),
    "result_step_count": ("=", "0..request.step_count and 0..16"),
    "result_step_ordinals": ("=", "unique, request-ordered values in 1..request.step_count"),
    "failed_step_ordinal": ("=", "null or a value in 1..request.step_count"),
}

VOLATILE_ID = re.compile(
    r"\btg_(?:event|handoff|checkpoint|review_request|review_receipt|"
    r"review_finding|verification_receipt)_[0-9a-f]{16}\b"
)
LIVE_STATUS_FIELDS = {
    "status",
    "current_status",
    "blocked_reason",
    "pause_reason",
    "completed_at",
    "completion_commit_hash",
}
LIVE_STATUS_KV = re.compile(
    r"(?i)^(?P<field>status|current_status|blocked_reason|pause_reason|"
    r"completed_at|completion_commit_hash)\s*(?::|=)\s*"
    r"(?P<value>\S(?:.*\S)?)\s*$"
)
LIVE_EXECUTION = re.compile(
    r"(?i)\bis\s+the\s+(?:current|next)\s+(?:sequential\s+)?"
    r"(?:task|unit)\b"
)
LIVE_EXECUTION_REVERSE = re.compile(
    r"(?i)\b(?:the\s+)?(?:current|next)\s+(?:sequential\s+)?(?:task|unit)"
    r"\s*(?:is|:|=)\s*TG-[A-Z0-9.]+\b"
)
TASK_ID = re.compile(r"(?i)\bTG-[A-Z0-9.]+\b")
TASK_STATUS_VALUES = {
    "ready",
    "in_progress",
    "review_pending",
    "blocked",
    "paused",
    "done",
}
CURRENT_UNITS = ("TG-M24.R5",)
NONCURRENT_UNITS = tuple(
    row[0].split(" /", 1)[0]
    for rows in (ROWS_M22, ROWS_M23, ROWS_M24, ROWS_DOC)
    for row in rows
    if row[0].split(" /", 1)[0] not in CURRENT_UNITS
) + ("TG-M24.1", "TG-M24.1A", "TG-M24.1B")
NONCURRENT_SUBJECTS = tuple(
    sorted(
        set(NONCURRENT_UNITS) | {"TG-M22", "TG-M23", "TG-M24", "TG-DOC"},
        key=lambda value: (-len(value), value),
    )
)
CURRENT_STATUS_CLAIM = re.compile(
    rf"\b(?:{'|'.join(re.escape(value) for value in NONCURRENT_SUBJECTS)})\b"
    r"(?:\s+(?:sequence|unit|units))?\s+(?:is|are)\s+(?:the\s+)?current\b",
    re.IGNORECASE,
)
M24_R3B_ALIAS = r"(?:TG-M24\.)?R3B"
M24_R4B_ALIAS = r"(?:TG-M24\.)?R4B"
M24_R5_ALIAS = r"(?:TG-M24\.)?R5"
M24_LATER_UNIT_ALIAS = r"(?:TG-M24\.)?(?:R[0-9]+[A-Z]?|[0-9]+[A-Z]?|CP[0-9]+)"
M24_R3B_TOKEN = rf"{M24_R3B_ALIAS}(?![A-Za-z0-9_-])"
M24_R4B_TOKEN = rf"{M24_R4B_ALIAS}(?![A-Za-z0-9_-])"
M24_R5_TOKEN = rf"{M24_R5_ALIAS}(?![A-Za-z0-9_-])"
M24_R3B_CURRENT_CLAIM = re.compile(
    rf"(?:\bcurrent\s+{M24_R3B_TOKEN}"
    rf"|\b{M24_R3B_TOKEN}(?:\s+(?:sequence|unit|route))?\s+"
    r"(?:(?:is|are|remains?)\s+(?:the\s+)?(?:sole\s+)?current\b"
    r"|owns?\s+(?:the\s+)?current(?:\s+formal)?\s+authority\b)"
    r"|\bcurrent\s+(?:formal\s+)?authority\s+belongs(?:\s+only)?\s+to\b"
    rf"\s+(?:the\s+)?{M24_R3B_TOKEN})",
    re.IGNORECASE,
)
M24_R4B_CURRENT_CLAIM = re.compile(
    rf"(?:\bcurrent\s+{M24_R4B_TOKEN}"
    rf"|\b{M24_R4B_TOKEN}(?:\s+(?:sequence|unit|route))?\s+"
    r"(?:(?:is|are|remains?)\s+(?:the\s+)?(?:sole\s+)?current\b"
    r"|owns?\s+(?:the\s+)?current(?:\s+formal)?\s+authority\b)"
    r"|\bcurrent\s+(?:formal\s+)?authority\s+belongs(?:\s+only)?\s+to\b"
    rf"\s+(?:the\s+)?{M24_R4B_TOKEN})",
    re.IGNORECASE,
)
M24_R4B_INACTIVE_CLAIM = re.compile(
    rf"(?:\binactive\s+{M24_R4B_TOKEN}"
    rf"|\b{M24_R4B_TOKEN}(?:\s+(?:sequence|unit|route))?\s+"
    r"(?:is|are|remains?)\s+(?:(?:an?|the)\s+)?inactive\b"
    rf"|\b{M24_R4B_TOKEN}(?:\s+through\s+{M24_LATER_UNIT_ALIAS}"
    r"|\s+and\s+(?:every\s+)?later\s+units?)\s+"
    r"(?:are|remain)\s+inactive\b)",
    re.IGNORECASE,
)
M24_R5_INACTIVE_CLAIM = re.compile(
    rf"(?:\binactive\s+{M24_R5_TOKEN}"
    rf"|\b{M24_R5_TOKEN}(?:\s+(?:sequence|unit|route))?\s+"
    r"(?:is|are|remains?)\s+(?:(?:an?|the)\s+)?inactive\b"
    rf"|\b{M24_R5_TOKEN}(?:\s+through\s+{M24_LATER_UNIT_ALIAS}"
    r"|\s+and\s+(?:every\s+)?later\s+units?)\s+"
    r"(?:are|remain)\s+inactive\b)",
    re.IGNORECASE,
)
UNIT_CURRENT_CLAIM = re.compile(
    r"\b(?:(?:an?|the|named)\s+)?(?:(?:tg-[a-z0-9.]+)\s+)?"
    r"(?:execution\s+)?units?\s+(?:is|are)\s+(?:the\s+)?current\b",
    re.IGNORECASE,
)
LIVE_REVIEW_TARGET_FIELDS = {
    "review_target_kind",
    "review_target_value",
    "review_target_base_revision",
    "review_target_generation",
}
LIVE_REVIEW_TARGET_KV = re.compile(
    r"(?i)^(?P<field>review_target_kind|review_target_value|"
    r"review_target_base_revision|review_target_generation)\s*(?::|=)\s*"
    r"(?P<value>\S(?:.*\S)?)\s*$"
)
MARKDOWN_OWNER_PATH = re.compile(
    r"(?i)(?:\.\./)*(?:[a-z0-9_.-]+/)*[a-z0-9_.-]+\.md"
    r"(?:#[a-z0-9_-]+)?"
)
ANCHOR = re.compile(r'^<a id="([a-z0-9_-]+)"></a>$')
ATX_HEADING = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+|$)(.*)$")
DIRECT_LINK = re.compile(
    r"(?<!!)\[([^\[\]\n]+)\]\(\s*"
    r"(?P<target><[^<>\n]+>|[^()\s]+)"
    r"(?:[ \t]+(?:\"[^\"\n]*\"|'[^'\n]*'|\([^()\n]*\)))?"
    r"[ \t]*\)"
)
REFERENCE_DEFINITION = re.compile(
    r"^ {0,3}\[([^\[\]\n]+)\]:[ \t]*"
    r"(?P<target><[^<>\n]+>|\S+)"
    r"(?:[ \t]+(?:\"[^\"\n]*\"|'[^'\n]*'|\([^()\n]*\)))?"
    r"[ \t]*$"
)
REFERENCE_LINK = re.compile(
    r"(?<!!)\[([^\[\]\n]+)\]\[([^\[\]\n]*)\]"
)
SHORTCUT_REFERENCE = re.compile(
    r"(?<!!)\[([^\[\]\n]+)\](?!\s*(?:[\[(]|:))"
)
EXTERNAL_TARGET = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
WINDOWS_DRIVE_TARGET = re.compile(r"^[A-Za-z]:[/\\]")
INLINE_TICKS = re.compile(re.escape(chr(96)) + r"+")
RAW_HTML_BLOCK_TAGS = (
    "address",
    "article",
    "aside",
    "base",
    "basefont",
    "blockquote",
    "body",
    "caption",
    "center",
    "col",
    "colgroup",
    "dd",
    "details",
    "dialog",
    "dir",
    "div",
    "dl",
    "dt",
    "fieldset",
    "figcaption",
    "figure",
    "footer",
    "form",
    "frame",
    "frameset",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "head",
    "header",
    "hr",
    "html",
    "iframe",
    "legend",
    "li",
    "link",
    "main",
    "menu",
    "menuitem",
    "nav",
    "noframes",
    "ol",
    "optgroup",
    "option",
    "p",
    "param",
    "pre",
    "script",
    "search",
    "section",
    "style",
    "summary",
    "table",
    "tbody",
    "td",
    "textarea",
    "tfoot",
    "th",
    "thead",
    "title",
    "tr",
    "track",
    "ul",
)
RAW_HTML_TYPE1_OPEN = re.compile(
    r"^\s*<(pre|script|style|textarea)(?=\s|>|$)", re.IGNORECASE
)
RAW_HTML_TYPE6_OPEN = re.compile(
    r"^\s*</?(" + "|".join(RAW_HTML_BLOCK_TAGS) + r")(?=\s|/?>|$)",
    re.IGNORECASE,
)
HTML_TAG_NAME = r"[A-Za-z][A-Za-z0-9-]*"
HTML_ATTRIBUTE_NAME = r"[A-Za-z_:][A-Za-z0-9_.:-]*"
HTML_UNQUOTED_VALUE = r'''[^\s"'=<>`]+'''
HTML_ATTRIBUTE = (
    rf"(?:\s+{HTML_ATTRIBUTE_NAME}(?:\s*=\s*(?:{HTML_UNQUOTED_VALUE}|"
    rf"'[^']*'|\"[^\"]*\"))?)"
)
RAW_HTML_TYPE7_LINE = re.compile(
    rf"^\s*(?:<{HTML_TAG_NAME}{HTML_ATTRIBUTE}*\s*/?>|"
    rf"</{HTML_TAG_NAME}\s*>)\s*$"
)
INLINE_HTML_TAG = re.compile(
    rf"</?{HTML_TAG_NAME}{HTML_ATTRIBUTE}*\s*/?>", re.IGNORECASE
)
SEMANTIC_EMPHASIS = re.compile(
    r"(?P<mark>\*\*|__|~~|\*)"
    r"(?P<body>[A-Za-z0-9][A-Za-z0-9_.:/+\-]*?)"
    r"(?P=mark)"
)
SEMANTIC_STRONG_SPAN = re.compile(
    r"(?<!\\)(?P<mark>\*\*|__)(?=\S)"
    r"(?P<body>[^\n]*?\S)(?P=mark)"
)
SEMANTIC_UNDERSCORE_EMPHASIS = re.compile(
    r"(?<![A-Za-z0-9_])_(?P<body>[A-Za-z0-9][A-Za-z0-9_.:/+\-]*?)_"
    r"(?![A-Za-z0-9_])"
)
MARKDOWN_CONTAINER_PREFIX = re.compile(
    r"^ {0,3}(?:(?:[-+*]|\d{1,9}[.)])(?:[ \t]+|$)|>[ \t]?)"
)
FENCE_QUOTE_PREFIX = re.compile(r"^ {0,3}>[ \t]?")
FENCE_LIST_PREFIX = re.compile(
    r"^ {0,3}(?:[-+*]|\d{1,9}[.)])(?:[ \t]{1,4}|$)"
)

# The title tokens declare a document's structural role without fixing its
# complete wording. Equivalent titles may add or reorder words.
ROLE_TITLE_TOKENS = {
    "AGENTS.md": ("agents",),
    "README.md": ("task-governance-tool",),
    AUTHORITY: ("authority", "index"),
    "docs/specification.md": ("product", "specification"),
    "docs/design.md": ("implementation", "design"),
    "plan.md": ("decisions", "issues"),
    EXECUTION_INDEX: ("execution", "contract", "index"),
    M22: ("m22", "evidence", "ledger", "accepted", "contract"),
    M23: ("m23", "derived", "evidence", "accepted", "contract"),
    M23_PROCESS: ("m23", "process", "safety", "contract"),
    M24: ("m24", "verification", "runner", "current", "contract"),
    HISTORY_INDEX: ("historical", "documentation", "index"),
    RELEASE_INSTALL: ("release", "install", "record"),
}

# Status is read from the first structural role block, not from a wording hash.
# Positive terms must be asserted rather than directly negated.  A separate
# negative relation is required where the owner declares that no unit is
# current; merely mentioning the word ``current`` is not sufficient.
ROLE_BANNER_STATUS = {
    EXECUTION_INDEX: (("mixed", "current", "conditional", "accepted", "inactive"), False),
    M22: (("accepted", "predecessor"), False),
    M23: (("accepted", "predecessor"), True),
    M23_PROCESS: (("delegated", "accepted"), True),
    M24: (
        (
            "current",
            "formal",
            "authority",
            "accepted",
            "predecessor",
            "superseded",
            "trusted-local",
            "explicit",
            "opt-in",
        ),
        False,
    ),
}


@dataclass(frozen=True, order=True)
class Issue:
    code: str
    subject: str
    message: str

    def to_data(self) -> dict[str, str]:
        return {"code": self.code, "subject": self.subject, "message": self.message}


@dataclass(frozen=True)
class Metric:
    path: str
    lines: int
    bytes: int

    def to_data(self) -> dict[str, int | str]:
        return self.__dict__.copy()


@dataclass(frozen=True)
class Link:
    line: int
    target: str
    route_eligible: bool


@dataclass(frozen=True)
class FenceContainer:
    # Each token is either a block quote marker or the exact content-column
    # indentation contributed by a list marker.  A closer must continue this
    # same container path; a fresh quote/list marker is code content.
    tokens: tuple[tuple[str, int], ...]


@dataclass
class Scan:
    lines: list[str]
    visible: list[str]
    semantic: list[str]
    headings: list[tuple[int, str, int]]
    anchors: dict[str, int]
    links: list[Link]
    fences: list[tuple[str, int, int]]
    quotes: list[tuple[str, ...]]


@dataclass(frozen=True)
class Result:
    metrics: tuple[Metric, ...]
    issues: tuple[Issue, ...]

    @property
    def ok(self) -> bool:
        return not self.issues

    def to_data(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "metrics": [metric.to_data() for metric in self.metrics],
            "issues": [issue.to_data() for issue in self.issues],
        }


@dataclass(frozen=True)
class SequenceSpec:
    path: str
    heading: str
    rows: tuple[tuple[str, ...], ...]
    headers: tuple[str, ...]
    identity_columns: int = 3


SEQUENCES = (
    SequenceSpec(
        M22,
        "## Accepted Sequence",
        ROWS_M22,
        ("Unit/order", "Task", "Dependency", "Bounded outcome and gate"),
    ),
    SequenceSpec(
        M23,
        "## Sequence Boundary",
        ROWS_M23,
        ("Unit/order", "Task", "Dependency"),
    ),
    SequenceSpec(
        M24,
        "## Sequence Boundary",
        ROWS_M24,
        (
            "Unit/order",
            "Task",
            "Dependency",
            "Purpose, permission boundary, and completion gate",
        ),
    ),
    SequenceSpec(
        "plan.md",
        "### TG-DOC Documentation Governance Sequence",
        ROWS_DOC,
        (
            "Unit/order",
            "Task",
            "Lane",
            "Dependency",
            "Authority status and successor gate",
        ),
        identity_columns=5,
    ),
)


@dataclass(frozen=True)
class DocumentationUnit:
    unit: str
    anchor: str
    heading_status: str


DOCUMENTATION_UNITS = (
    DocumentationUnit(
        "TG-DOC.2",
        "tg-doc-2",
        "accepted",
    ),
    DocumentationUnit(
        "TG-DOC.3",
        "tg-doc-3",
        "inactive",
    ),
)


def _is_link_like(path: Path) -> bool:
    try:
        data = os.lstat(path)
    except OSError:
        return False
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return path.is_symlink() or bool(
        getattr(data, "st_file_attributes", 0) & reparse
    )


def _safe_file(root: Path, relative: str) -> Path | None:
    if "\\" in relative or relative.startswith("/"):
        return None
    current = root
    for part in relative.split("/"):
        if part in {"", ".", ".."}:
            return None
        try:
            names = {entry.name for entry in current.iterdir()}
        except OSError:
            return None
        if part not in names:
            return None
        current = current / part
        if _is_link_like(current):
            return None
    try:
        return current if current.is_file() else None
    except OSError:
        return None


def _read(
    root: Path, relative: str, issues: list[Issue]
) -> tuple[bytes, str] | None:
    path = _safe_file(root, relative)
    if path is None:
        issues.append(
            Issue("document_unavailable", relative, "required regular file is unavailable")
        )
        return None
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8").replace("\r\n", "\n")
    except (OSError, UnicodeError):
        issues.append(
            Issue("document_encoding", relative, "document must be strict UTF-8")
        )
        return None
    if raw.startswith(b"\xef\xbb\xbf") or not raw.endswith(b"\n"):
        issues.append(
            Issue(
                "document_encoding",
                relative,
                "document must be BOM-free UTF-8 with final newline",
            )
        )
    return raw, text


def _is_escaped(line: str, position: int) -> bool:
    backslashes = 0
    position -= 1
    while position >= 0 and line[position] == "\\":
        backslashes += 1
        position -= 1
    return backslashes % 2 == 1


def _markup_views(
    line: str, inline_carry: str = "", html_comment: bool = False
) -> tuple[str, str, str, bool]:
    """Return visible and semantic views while preserving source offsets.

    The visible view masks inline code and comments.  The semantic view masks
    comments and inline delimiters but retains inline-code payload, allowing
    structural checks to see identifiers without trusting hidden comments.
    """

    masked = list(line)
    semantic = list(line)
    position = 0
    while position < len(line):
        if html_comment:
            closing = line.find("-->", position)
            if closing < 0:
                masked[position:] = " " * (len(line) - position)
                semantic[position:] = " " * (len(line) - position)
                return "".join(masked), "".join(semantic), inline_carry, True
            end = closing + 3
            masked[position:end] = " " * (end - position)
            semantic[position:end] = " " * (end - position)
            position = end
            html_comment = False
            continue

        if inline_carry:
            closing = next(
                (
                    match
                    for match in INLINE_TICKS.finditer(line, position)
                    if match.group(0) == inline_carry
                    and not _is_escaped(line, match.start())
                ),
                None,
            )
            if closing is None:
                masked[position:] = " " * (len(line) - position)
                return "".join(masked), "".join(semantic), inline_carry, False
            masked[position : closing.end()] = " " * (closing.end() - position)
            semantic[closing.start() : closing.end()] = " " * len(inline_carry)
            position = closing.end()
            inline_carry = ""
            continue

        if line.startswith("<!--", position):
            closing = line.find("-->", position + 4)
            if closing < 0:
                masked[position:] = " " * (len(line) - position)
                semantic[position:] = " " * (len(line) - position)
                return "".join(masked), "".join(semantic), "", True
            end = closing + 3
            masked[position:end] = " " * (end - position)
            semantic[position:end] = " " * (end - position)
            position = end
            continue

        start = INLINE_TICKS.match(line, position)
        if start is None or _is_escaped(line, position):
            position += 1
            continue
        delimiter = start.group(0)
        closing = next(
            (
                match
                for match in INLINE_TICKS.finditer(line, start.end())
                if match.group(0) == delimiter
                and not _is_escaped(line, match.start())
            ),
            None,
        )
        if closing is None:
            masked[position:] = " " * (len(line) - position)
            semantic[position : start.end()] = " " * len(delimiter)
            return "".join(masked), "".join(semantic), delimiter, False
        masked[position : closing.end()] = " " * (closing.end() - position)
        semantic[position : start.end()] = " " * len(delimiter)
        semantic[closing.start() : closing.end()] = " " * len(delimiter)
        position = closing.end()
    return "".join(masked), "".join(semantic), inline_carry, html_comment


def _mask_markup(
    line: str, inline_carry: str = "", html_comment: bool = False
) -> tuple[str, str, bool]:
    """Mask inline code and HTML comments while preserving source offsets."""

    masked, _semantic, inline_carry, html_comment = _markup_views(
        line, inline_carry, html_comment
    )
    return masked, inline_carry, html_comment


def _mask_inline(line: str, carry: str = "") -> tuple[str, str]:
    """Compatibility wrapper for callers that need only inline-code masking."""

    masked, carry, _html_comment = _mask_markup(line, carry, False)
    return masked, carry


def _semantic_prose(text: str, *, include_link_targets: bool = False) -> str:
    """Remove inline framing; optionally retain direct-link destinations."""

    normalized = DIRECT_LINK.sub(
        lambda match: (
            f"{match.group(1)} {_markdown_link_target(match)}"
            if include_link_targets
            else match.group(1)
        ),
        text,
    )
    normalized = REFERENCE_LINK.sub(lambda match: match.group(1), normalized)
    normalized = SHORTCUT_REFERENCE.sub(lambda match: match.group(1), normalized)
    normalized = INLINE_HTML_TAG.sub("", normalized)
    while True:
        replaced = SEMANTIC_STRONG_SPAN.sub(
            lambda match: match.group("body"), normalized
        )
        replaced = SEMANTIC_EMPHASIS.sub(lambda match: match.group("body"), replaced)
        replaced = SEMANTIC_UNDERSCORE_EMPHASIS.sub(
            lambda match: match.group("body"), replaced
        )
        if replaced == normalized:
            return normalized
        normalized = replaced


def _reference_label(text: str) -> str:
    return " ".join(text.split()).casefold()


def _markdown_link_target(match: re.Match[str]) -> str:
    target = match.group("target")
    return target[1:-1] if target.startswith("<") and target.endswith(">") else target


def _fence_opener(line: str) -> tuple[str, str] | None:
    stripped = line.lstrip(" ")
    if len(line) - len(stripped) > 3 or not stripped:
        return None
    marker_char = ""
    for candidate in (chr(96), "~"):
        if stripped.startswith(candidate * 3):
            marker_char = candidate
            break
    if not marker_char:
        return None
    length = 0
    while length < len(stripped) and stripped[length] == marker_char:
        length += 1
    marker = marker_char * length
    return marker, stripped[length:].strip()


def _fence_closes(line: str, marker: str) -> bool:
    stripped = line.lstrip(" ")
    if len(line) - len(stripped) > 3 or not stripped.startswith(marker):
        return False
    marker_char = marker[0]
    length = 0
    while length < len(stripped) and stripped[length] == marker_char:
        length += 1
    return length >= len(marker) and not stripped[length:].strip()


def _fence_opener_with_container(
    line: str,
) -> tuple[str, str, FenceContainer] | None:
    content = line.expandtabs(4)
    tokens: list[tuple[str, int]] = []
    while True:
        quote = FENCE_QUOTE_PREFIX.match(content)
        if quote is not None:
            prefix = content[: quote.end()]
            leading_indent = len(prefix) - len(prefix.lstrip(" "))
            tokens.append(("quote", leading_indent))
            content = content[quote.end() :]
            continue
        item = FENCE_LIST_PREFIX.match(content)
        if item is not None:
            tokens.append(("list", item.end()))
            content = content[item.end() :]
            continue
        break
    residual_indent = len(content) - len(content.lstrip(" "))
    if residual_indent:
        tokens.append(("indent", residual_indent))
    opener = _fence_opener(content)
    if opener is None and residual_indent >= 4:
        # Four or more columns are normally indented code at top level, but
        # can be a fence inside a list whose preceding item established the
        # container on an earlier line.  Treat that ambiguity as a fence and
        # require the same indentation to close, which fails closed.
        opener = _fence_opener(content[residual_indent:])
    if opener is None:
        return None
    marker, info = opener
    return marker, info, FenceContainer(tuple(tokens))


def _fence_container_content(
    line: str, container: FenceContainer
) -> str | None:
    content = line.expandtabs(4)
    for kind, width in container.tokens:
        if kind == "quote":
            if len(content) < width or content[:width] != " " * width:
                return None
            content = content[width:]
            quote = FENCE_QUOTE_PREFIX.match(content)
            if quote is None:
                return None
            content = content[quote.end() :]
            continue
        # A list-contained fence closes from the item's continuation column.
        # Never consume a fresh list marker: inside a fence it is code bytes.
        if len(content) < width or content[:width] != " " * width:
            return None
        content = content[width:]
    return content


def _fence_closes_in_container(
    line: str, marker: str, container: FenceContainer
) -> bool:
    content = _fence_container_content(line, container)
    return content is not None and _fence_closes(content, marker)


def _markdown_container_content(line: str) -> str:
    content = line
    while True:
        prefix = MARKDOWN_CONTAINER_PREFIX.match(content)
        if prefix is None:
            return content
        content = content[prefix.end() :]


def _html_block_step(line: str, active_tag: str) -> tuple[bool, str]:
    if active_tag:
        kind, _separator, detail = active_tag.partition(":")
        if kind in {"type6", "type7"}:
            return True, "" if not line.strip() else active_tag
        if kind == "type1":
            closed = re.search(
                rf"</{re.escape(detail)}\s*>", line, re.IGNORECASE
            )
            return True, "" if closed else active_tag
        marker = {
            "type2": "-->",
            "type3": "?>",
            "type4": ">",
            "type5": "]]>",
        }[kind]
        return True, "" if marker in line else active_tag

    if ANCHOR.fullmatch(line):
        return False, ""
    container_content = _markdown_container_content(line)
    stripped = container_content.lstrip()
    type1 = RAW_HTML_TYPE1_OPEN.match(container_content)
    if type1 is not None:
        tag = type1.group(1).lower()
        closed = re.search(rf"</{re.escape(tag)}\s*>", line, re.IGNORECASE)
        return True, "" if closed else f"type1:{tag}"
    for opener, marker, state in (
        ("<!--", "-->", "type2:"),
        ("<?", "?>", "type3:"),
        ("<![CDATA[", "]]>", "type5:"),
    ):
        if stripped.startswith(opener):
            return True, "" if marker in stripped[len(opener) :] else state
    if re.match(r"^<![A-Z]", stripped):
        return True, "" if ">" in stripped[2:] else "type4:"
    if RAW_HTML_TYPE6_OPEN.match(container_content):
        return True, "type6:"
    if RAW_HTML_TYPE7_LINE.fullmatch(container_content):
        return True, "type7:"
    return False, ""


def _scan(relative: str, text: str, issues: list[Issue]) -> Scan:
    lines = text.splitlines()
    visible = [""] * len(lines)
    semantic = [""] * len(lines)
    headings: list[tuple[int, str, int]] = []
    anchors: dict[str, int] = {}
    links: list[Link] = []
    reference_definitions: dict[str, str] = {}
    reference_uses: list[tuple[int, str, bool]] = []
    fences: list[tuple[str, int, int]] = []
    quote_groups: list[list[str]] = []
    fence_marker = ""
    fence_info = ""
    fence_container = FenceContainer(())
    fence_poisoned = False
    fence_start = -1
    inline_carry = ""
    html_comment = False
    raw_html_tag = ""
    quote_html_tag = ""
    quote_active = False

    for index, line in enumerate(lines):
        if fence_marker:
            if not line.startswith(">"):
                quote_active = False
            container_content = _fence_container_content(line, fence_container)
            if container_content is None:
                # A container-owned fence ended or changed parent.  This
                # bounded scanner does not reparse CommonMark block state, so
                # keep the remainder inert instead of letting a later
                # look-alike closer re-enable authority text.
                if not fence_poisoned:
                    issues.append(
                        Issue(
                            "markdown_structure",
                            relative,
                            f"line {index + 1}: fenced block left its opening "
                            "container without a compatible close",
                        )
                    )
                fence_poisoned = True
            elif not fence_poisoned and _fence_closes(
                container_content, fence_marker
            ):
                fences.append((fence_info, fence_start, index))
                fence_marker = ""
                fence_info = ""
                fence_container = FenceContainer(())
                fence_poisoned = False
                fence_start = -1
            continue

        is_quote = line.startswith(">")
        if is_quote:
            payload = line[2:] if line.startswith("> ") else line[1:]
        else:
            payload = line
            quote_active = False

        if raw_html_tag:
            _inert_html, raw_html_tag = _html_block_step(line, raw_html_tag)
            quote_active = False
            continue
        if is_quote:
            inert_html, quote_html_tag = _html_block_step(payload, quote_html_tag)
        else:
            quote_html_tag = ""
            inert_html, raw_html_tag = _html_block_step(payload, raw_html_tag)
        if inert_html:
            continue

        indented_code = payload.startswith("\t") or payload.startswith("    ")
        if not (html_comment or inline_carry):
            opener = _fence_opener_with_container(line)
            if opener is not None:
                if not is_quote:
                    quote_active = False
                fence_marker, fence_info, fence_container = opener
                fence_poisoned = False
                fence_start = index
                continue

        if indented_code and not (html_comment or inline_carry):
            quote_active = False
            continue

        masked, semantic_view, inline_carry, html_comment = _markup_views(
            payload, inline_carry, html_comment
        )
        if indented_code:
            quote_active = False
            continue

        if is_quote and masked.strip():
            if not quote_active:
                quote_groups.append([])
            quote_groups[-1].append("> " + masked)
            quote_active = True
        elif is_quote:
            quote_active = False

        anchor = None if is_quote else ANCHOR.fullmatch(masked)
        if anchor:
            name = anchor.group(1)
            if name in anchors:
                issues.append(
                    Issue(
                        "anchor_duplicate",
                        relative,
                        f"line {index + 1}: duplicate explicit anchor",
                    )
                )
            anchors[name] = index
            visible[index] = masked
            semantic[index] = semantic_view
            continue

        visible[index] = " " + masked if is_quote else masked
        semantic[index] = " " + semantic_view if is_quote else semantic_view
        heading = None if is_quote else ATX_HEADING.fullmatch(masked)
        if heading:
            level = len(heading.group(1))
            body = re.sub(r"[ \t]+#+[ \t]*$", "", heading.group(2)).strip()
            if body:
                headings.append((level, "#" * level + " " + body, index))

        definition = None if is_quote else REFERENCE_DEFINITION.fullmatch(masked)
        if definition is not None:
            label = _reference_label(definition.group(1))
            target = _markdown_link_target(definition)
            previous = reference_definitions.get(label)
            if previous is not None and previous != target:
                issues.append(
                    Issue(
                        "link_reference",
                        relative,
                        f"line {index + 1}: duplicate reference label is ambiguous",
                    )
                )
            else:
                reference_definitions.setdefault(label, target)

        reference_source = list(masked)
        for match in DIRECT_LINK.finditer(masked):
            if not _is_escaped(masked, match.start()):
                links.append(
                    Link(
                        index,
                        _markdown_link_target(match),
                        not is_quote and "<" not in masked and ">" not in masked,
                    )
                )
                reference_source[match.start() : match.end()] = " " * (
                    match.end() - match.start()
                )

        if definition is None:
            reference_text = "".join(reference_source)
            full_spans: list[tuple[int, int]] = []
            for match in REFERENCE_LINK.finditer(reference_text):
                if _is_escaped(reference_text, match.start()):
                    continue
                label = _reference_label(match.group(2) or match.group(1))
                reference_uses.append(
                    (
                        index,
                        label,
                        False,
                    )
                )
                full_spans.append((match.start(), match.end()))
            shortcut_source = list(reference_text)
            for start, end in full_spans:
                shortcut_source[start:end] = " " * (end - start)
            shortcut_text = "".join(shortcut_source)
            for match in SHORTCUT_REFERENCE.finditer(shortcut_text):
                if not _is_escaped(shortcut_text, match.start()):
                    reference_uses.append(
                        (
                            index,
                            _reference_label(match.group(1)),
                            False,
                        )
                    )

    for line, label, route_eligible in reference_uses:
        target = reference_definitions.get(label)
        if target is not None:
            links.append(Link(line, target, route_eligible))

    return Scan(
        lines,
        visible,
        semantic,
        headings,
        anchors,
        links,
        fences,
        [tuple(group) for group in quote_groups],
    )


def _section_bounds(scan: Scan, heading: str) -> tuple[int, int] | None:
    matches = [(level, index) for level, line, index in scan.headings if line == heading]
    if len(matches) != 1:
        return None
    level, start = matches[0]
    end = len(scan.lines)
    for next_level, _line, index in scan.headings:
        if index > start and next_level <= level:
            end = index
            break
    return start, end


def _semantic_section_blocks(scan: Scan, heading: str) -> tuple[str, ...] | None:
    bounds = _section_bounds(scan, heading)
    if bounds is None:
        return None
    blocks: list[str] = []
    current: list[str] = []
    for position in range(bounds[0] + 1, bounds[1]):
        semantic = _semantic_prose(
            scan.semantic[position], include_link_targets=True
        )
        if not scan.lines[position].strip():
            if current:
                blocks.append(" ".join(current))
                current = []
            continue
        if not semantic.strip():
            continue
        starts_item = re.match(
            r"^ {0,3}(?:[-+*]|\d{1,9}[.)])(?:[ \t]+|$)", semantic
        )
        if starts_item and current:
            blocks.append(" ".join(current))
            current = []
        current.append(" ".join(semantic.lower().split()))
    if current:
        blocks.append(" ".join(current))
    return tuple(blocks)


def _relations_present(
    blocks: tuple[str, ...] | None,
    relations: tuple[tuple[tuple[str, ...], ...], ...],
) -> bool:
    return blocks is not None and all(
        any(_relation_present(block, relation) for block in blocks)
        for relation in relations
    )


def _relation_occurrence_negated(text: str, start: int, end: int) -> bool:
    if _directly_negated(text, start):
        return True
    clause_start, _clause_end = _semantic_clause_bounds(text, start)
    prefix = text[max(clause_start, start - 96) : start]
    scope_breaks = tuple(re.finditer(r",\s*(?:and|but|yet|then)\b", prefix))
    if scope_breaks:
        prefix = prefix[scope_breaks[-1].end() :]
    if re.search(
        r"\b(?:(?:do|does|did|can|could|should|must|may|will|would)\s+not|"
        r"never|avoid|without)\b[^.;]{0,80}$",
        prefix,
    ):
        return True
    suffix = text[end : end + 56]
    return bool(
        re.match(
            r"\s+(?:(?:is|are|was|were|remains?|becomes?)\s+"
            r"(?:not|never|no\s+longer)\b|"
            r"(?:do|does|did|can|could|should|must|may|will|would)\s+not\b|"
            r"(?:only\s+)?as\s+"
            r"(?:an?\s+)?(?:reference|example|non-authority)\b)",
            suffix,
        )
    )


def _positive_relation_term(text: str, term: str) -> bool:
    matches = tuple(re.finditer(re.escape(term), text))
    return bool(matches) and all(
        not _relation_occurrence_negated(text, match.start(), match.end())
        for match in matches
    )


def _relation_present(
    text: str, relation: tuple[tuple[str, ...], ...]
) -> bool:
    return all(
        any(_positive_relation_term(text, alternative) for alternative in group)
        for group in relation
    )


def _markdown_owner_paths(text: str) -> set[str]:
    return {
        match.group(0).lstrip("./").split("#", 1)[0].lower()
        for match in MARKDOWN_OWNER_PATH.finditer(text)
    }


def _semantic_clause_bounds(text: str, position: int) -> tuple[int, int]:
    path_positions = {
        offset
        for match in MARKDOWN_OWNER_PATH.finditer(text)
        for offset in range(match.start(), match.end())
    }
    separators = [
        index
        for index, character in enumerate(text)
        if character == ";" or (character == "." and index not in path_positions)
    ]
    before = [index for index in separators if index < position]
    after = [index for index in separators if index >= position]
    return (max(before) + 1 if before else 0, min(after) if after else len(text))


def _source_owner_relation_present(
    block: str, owner: str, topics: tuple[str, ...]
) -> bool:
    if _markdown_owner_paths(block) != _markdown_owner_paths(owner):
        return False
    if not _owner_asserted(block, owner, topics):
        return False
    return not any(
        candidate != owner and _owner_asserted(block, candidate, topics)
        for candidate in SOURCE_KNOWN_OWNERS
    )


def _owner_asserted(
    block: str, owner: str, topics: tuple[str, ...] = ()
) -> bool:
    for match in re.finditer(re.escape(owner), block):
        if _relation_occurrence_negated(block, match.start(), match.end()):
            continue
        clause_start, clause_end = _semantic_clause_bounds(block, match.start())
        before = block[clause_start : match.start()]
        after = block[match.end() : clause_end]
        clause = block[clause_start:clause_end]
        if _markdown_owner_paths(clause) != _markdown_owner_paths(owner):
            continue
        if topics and not all(
            _positive_relation_term(clause, topic) for topic in topics
        ):
            continue
        if re.match(
            r"\s+(?:only\s+)?as\s+(?:an?\s+)?"
            r"(?:reference|example|non-authority)\b",
            after,
        ):
            continue
        directed_match = re.search(
            r"\b(?:follow|prefer|read|use|consult|select|route(?:d)?\s+by|"
            r"govern(?:ed)?\s+by)\b[^.;]{0,160}$",
            before,
        )
        directed_to_owner = bool(
            directed_match
            and not _relation_occurrence_negated(
                before, directed_match.start(), directed_match.end()
            )
        )
        owner_assertion = re.search(
            r"^.{0,80}\b(?:owns?|governs?|controls?|"
            r"is\s+(?:the\s+)?(?:owner|authority))\b",
            after,
        )
        if directed_to_owner or owner_assertion:
            return True
    return False


def _expected_registry() -> dict[str, object]:
    return {
        "schema": "taskgov-document-authority-v5",
        "mandatory_start": ["AGENTS.md", AUTHORITY, "live_task_contract"],
        "current": ["docs/specification.md", "docs/design.md", "plan.md"],
        "mixed_execution": [
            {
                "path": M22,
                "route_anchor": "tg-m22-sequence",
                "current_units": [],
                "inactive_units": [],
            },
            {
                "path": M23,
                "route_anchor": "tg-m23-derived-evidence",
                "current_units": [],
                "inactive_units": [],
                "detail_routes": [
                    {
                        "path": M23_PROCESS,
                        "route_anchor": "tg-m23-process-safety",
                        "parent_anchor": "tg-m23-1",
                        "owner_scope": "windows_process_private_temp_atomic_publication",
                    }
                ],
            },
            {
                "path": M24,
                "route_anchor": "tg-m24-verification-runner",
                "current_units": ["TG-M24.R5"],
                "inactive_units": [
                    "TG-M24.2A",
                    "TG-M24.2B",
                    "TG-M24.2C",
                    "TG-M24.2D",
                    "TG-M24.3",
                    "TG-M24.4A",
                    "TG-M24.4B",
                    "TG-M24.4C",
                    "TG-M24.4D",
                    "TG-M24.CP4",
                ],
                "superseded_units": ["TG-M24.1B"],
            },
        ],
        "documentation_sequence": {
            "path": "plan.md",
            "route_anchor": "tg-doc-sequence",
            "current_units": [],
            "inactive_units": ["TG-DOC.3"],
        },
        "conditional": [],
        "history_index": HISTORY_INDEX,
    }


def _semantic_equal(observed: object, expected: object) -> bool:
    if isinstance(expected, dict):
        return (
            isinstance(observed, dict)
            and set(observed) == set(expected)
            and all(_semantic_equal(observed[key], value) for key, value in expected.items())
        )
    if isinstance(expected, list):
        return (
            isinstance(observed, list)
            and len(observed) == len(expected)
            and all(
                _semantic_equal(left, right)
                for left, right in zip(observed, expected)
            )
        )
    return type(observed) is type(expected) and observed == expected


def _registry(scan: Scan, issues: list[Issue]) -> dict[str, object] | None:
    bounds = _section_bounds(scan, "## Machine-Readable Registry")
    blocks = (
        []
        if bounds is None
        else [
            block
            for block in scan.fences
            if bounds[0] < block[1] < block[2] < bounds[1]
            and block[0].strip() == "json"
        ]
    )
    parsed: object = None
    duplicate = False
    if len(blocks) == 1:

        def pairs(values: list[tuple[str, object]]) -> dict[str, object]:
            nonlocal duplicate
            result: dict[str, object] = {}
            for key, value in values:
                duplicate |= key in result
                result[key] = value
            return result

        try:
            parsed = json.loads(
                "\n".join(scan.lines[blocks[0][1] + 1 : blocks[0][2]]),
                object_pairs_hook=pairs,
            )
        except (json.JSONDecodeError, TypeError, ValueError):
            parsed = None
    expected = _expected_registry()
    if duplicate or not _semantic_equal(parsed, expected):
        issues.append(
            Issue(
                "authority_registry",
                AUTHORITY,
                "machine registry differs from the semantic authority graph",
            )
        )
        return None
    assert isinstance(parsed, dict)
    return parsed


def _resolve(root: Path, source: str, target: str) -> tuple[str, str] | None:
    if any(token in target for token in ("%", "?", "\\", ":", " ")) or "//" in target:
        return None
    path_text, separator, fragment = target.partition("#")
    if separator and (not fragment or not re.fullmatch(r"[a-z0-9_-]+", fragment)):
        return None
    if path_text and not re.fullmatch(r"[A-Za-z0-9._/-]+", path_text):
        return None
    if not path_text:
        relative = source
    else:
        parts = path_text.split("/")
        current = Path(source).parent
        leading = True
        for part in parts:
            if part == ".." and leading:
                current = current.parent
            elif part in {"", ".", ".."}:
                return None
            else:
                leading = False
                current /= part
        relative = current.as_posix()
    if (
        relative.startswith("../")
        or relative == ".."
        or _safe_file(root, relative) is None
    ):
        return None
    return relative, fragment


def _is_external_target(target: str) -> bool:
    return (
        not WINDOWS_DRIVE_TARGET.match(target)
        and (bool(EXTERNAL_TARGET.match(target)) or target.startswith("//"))
    )


def _heading_slugs(scan: Scan) -> set[str]:
    slugs: set[str] = set()
    counts: dict[str, int] = {}
    for _level, heading, _position in scan.headings:
        title = heading.lstrip("#").strip().lower()
        base = re.sub(r"[^\w\s-]", "", title, flags=re.UNICODE)
        base = re.sub(r"\s+", "-", base).strip("-")
        if not base:
            continue
        ordinal = counts.get(base, 0)
        counts[base] = ordinal + 1
        slugs.add(base if ordinal == 0 else f"{base}-{ordinal}")
    return slugs


def _links_and_routes(
    root: Path, scans: dict[str, Scan], issues: list[Issue]
) -> None:
    heading_slugs = {relative: _heading_slugs(scan) for relative, scan in scans.items()}
    for relative, scan in scans.items():
        for link in scan.links:
            if _is_external_target(link.target):
                continue
            resolved = _resolve(root, relative, link.target)
            if resolved is None:
                issues.append(
                    Issue(
                        "link_target",
                        relative,
                        f"line {link.line + 1}: local link target is unsafe or missing",
                    )
                )
                continue
            target_path, fragment = resolved
            if fragment and (
                target_path not in scans
                or (
                    fragment not in scans[target_path].anchors
                    and fragment not in heading_slugs[target_path]
                )
            ):
                issues.append(
                    Issue(
                        "link_anchor",
                        relative,
                        f"line {link.line + 1}: fragment must name a reachable anchor or heading",
                    )
                )

    for relative, heading, expected in ROUTE_SECTIONS:
        scan = scans[relative]
        bounds = _section_bounds(scan, heading)
        observed = (
            ()
            if bounds is None
            else tuple(
                link.target
                for link in scan.links
                if bounds[0] < link.line < bounds[1] and link.route_eligible
            )
        )
        valid = Counter(observed) == Counter(expected)
        if valid:
            for target in expected:
                resolved = _resolve(root, relative, target)
                if resolved is None:
                    valid = False
                    break
                target_path, fragment = resolved
                if fragment and (
                    target_path not in scans
                    or fragment not in scans[target_path].anchors
                ):
                    valid = False
                    break
        if not valid:
            issues.append(
                Issue(
                    "authority_route",
                    relative,
                    f"{heading} differs from the semantic route set",
                )
            )


def _cells(line: str) -> tuple[str, ...]:
    return tuple(cell.strip() for cell in line.strip()[1:-1].split("|"))


def _section_table_range(scan: Scan, heading: str) -> tuple[int, int] | None:
    bounds = _section_bounds(scan, heading)
    if bounds is None:
        return None
    start = next(
        (
            index
            for index in range(bounds[0] + 1, bounds[1])
            if scan.visible[index].startswith("|")
        ),
        None,
    )
    if start is None:
        return None
    end = start
    while end < bounds[1] and scan.visible[end].startswith("|"):
        end += 1
    return start, end


def _sequence_table(scan: Scan, heading: str) -> tuple[str, ...] | None:
    table_range = _section_table_range(scan, heading)
    if table_range is None:
        return None
    return tuple(scan.lines[table_range[0] : table_range[1]])


def _semantic_table(scan: Scan, heading: str) -> tuple[str, ...] | None:
    table_range = _section_table_range(scan, heading)
    if table_range is None:
        return None
    return tuple(scan.semantic[table_range[0] : table_range[1]])


def _sequences(scans: dict[str, Scan], issues: list[Issue]) -> None:
    for spec in SEQUENCES:
        scan = scans[spec.path]
        table = _sequence_table(scan, spec.heading)
        valid = table is not None and len(table) == len(spec.rows) + 2
        if valid:
            header = _cells(table[0])
            separator = _cells(table[1])
            valid = (
                header == spec.headers
                and len(separator) == len(header)
                and all(re.fullmatch(r":?-{3,}:?", cell) for cell in separator)
            )
        if valid:
            observed: list[tuple[str, ...]] = []
            for row in table[2:]:
                cells = _cells(row)
                if len(cells) != len(header):
                    valid = False
                    break
                observed.append(
                    tuple(
                        cell.strip(chr(96))
                        for cell in cells[: spec.identity_columns]
                    )
                )
            valid = valid and tuple(observed) == spec.rows
        if valid and table is not None:
            structural_rows = table[2:]
            valid = all(
                sum(task_id in row for row in structural_rows) == 1
                for task_id in (row[1] for row in spec.rows)
            )
        if not valid:
            issues.append(
                Issue(
                    "sequence_contract",
                    spec.path,
                    f"{spec.heading} Task identity, order, or dependency drifted",
                )
            )

    m24 = scans[M24]
    required_m24_anchors = (
        "tg-m24-1",
        "tg-m24-1a",
        "tg-m24-1b",
        "tg-m24-r1",
        "tg-m24-r2a",
        "tg-m24-r2b",
        "tg-m24-r2c",
        "tg-m24-r4a",
        "tg-m24-r3a",
        "tg-m24-r3b",
        "tg-m24-r4b",
        "tg-m24-r5",
        "tg-m24-2a",
        "tg-m24-2b",
        "tg-m24-2c",
        "tg-m24-2",
        "tg-m24-3",
        "tg-m24-4a",
        "tg-m24-4b",
        "tg-m24-4c",
        "tg-m24-4",
        "tg-m24-cp4",
    )
    if any(anchor not in m24.anchors for anchor in required_m24_anchors):
        issues.append(
            Issue(
                "sequence_contract",
                M24,
                "M24 unit anchors are incomplete",
            )
        )
    else:
        positions = tuple(m24.anchors[anchor] for anchor in required_m24_anchors)
        if positions != tuple(sorted(positions)) or len(set(positions)) != len(
            positions
        ):
            issues.append(
                Issue(
                    "sequence_contract",
                    M24,
                    "M24 unit anchors are out of order",
                )
            )


def _bounded_reading_controls(
    scans: dict[str, Scan], issues: list[Issue]
) -> None:
    agents = scans["AGENTS.md"]
    source_blocks = _semantic_section_blocks(agents, "## Source Of Truth")
    reread_blocks = _semantic_section_blocks(agents, "## Reread Rule")
    valid_agents = (
        _relations_present(source_blocks, SOURCE_START_RELATIONS)
        and source_blocks is not None
        and all(
            any(
                _source_owner_relation_present(block, owner, topics)
                for block in source_blocks
            )
            for owner, topics in SOURCE_OWNER_RELATIONS
        )
        and _relations_present(reread_blocks, REREAD_RELATIONS)
    )
    if not valid_agents:
        issues.append(
            Issue(
                "authority_route",
                "AGENTS.md",
                "bounded start, owner selection, or reread relations are incomplete",
            )
        )

    authority = scans[AUTHORITY]
    table = _semantic_table(authority, "## Trigger Routing")
    valid_routes = table is not None and len(table) == len(TRIGGER_ROUTE_RELATIONS) + 2
    rows: tuple[tuple[str, ...], ...] = ()
    if valid_routes:
        header = tuple(cell.lower() for cell in _cells(table[0]))
        separator = _cells(table[1])
        valid_routes = (
            header == ("trigger", "required selective route")
            and len(separator) == 2
            and all(re.fullmatch(r":?-{3,}:?", cell) for cell in separator)
        )
        if valid_routes:
            parsed: list[tuple[str, ...]] = []
            for raw_row in table[2:]:
                cells = tuple(cell.lower() for cell in _cells(raw_row))
                if len(cells) != 2:
                    valid_routes = False
                    break
                parsed.append(cells)
            rows = tuple(parsed)
    if valid_routes:
        relation_counts: Counter[int] = Counter()
        for trigger, route in rows:
            matches = [
                index
                for index, (identity, _owners) in enumerate(TRIGGER_ROUTE_RELATIONS)
                if _relation_present(
                    trigger, tuple((term,) for term in identity)
                )
            ]
            if len(matches) != 1:
                valid_routes = False
                break
            relation_index = matches[0]
            relation_counts[relation_index] += 1
            owners = TRIGGER_ROUTE_RELATIONS[relation_index][1]
            owner_relation = tuple((term,) for term in owners)
            if not _relation_present(route, owner_relation):
                valid_routes = False
                break
            expected_paths = _markdown_owner_paths(" ".join(owners))
            observed_paths = _markdown_owner_paths(route)
            if observed_paths != expected_paths:
                valid_routes = False
                break
        valid_routes = valid_routes and relation_counts == Counter(
            range(len(TRIGGER_ROUTE_RELATIONS))
        )
    if not valid_routes:
        issues.append(
            Issue(
                "authority_route",
                AUTHORITY,
                "trigger-to-owner selective routing is incomplete or ambiguous",
            )
        )


def _registry_routes(
    scans: dict[str, Scan], registry: dict[str, object], issues: list[Issue]
) -> None:
    route_objects: list[dict[str, object]] = []
    mixed = registry["mixed_execution"]
    assert isinstance(mixed, list)
    route_objects.extend(item for item in mixed if isinstance(item, dict))
    documentation = registry["documentation_sequence"]
    assert isinstance(documentation, dict)
    route_objects.append(documentation)

    for route in route_objects:
        path = route["path"]
        anchor = route["route_anchor"]
        if (
            not isinstance(path, str)
            or not isinstance(anchor, str)
            or path not in scans
            or anchor not in scans[path].anchors
        ):
            issues.append(
                Issue(
                    "authority_route",
                    AUTHORITY,
                    "registered owner path or route anchor is unavailable",
                )
            )
        details = route.get("detail_routes", [])
        if isinstance(details, list):
            for detail in details:
                if not isinstance(detail, dict):
                    continue
                detail_path = detail.get("path")
                detail_anchor = detail.get("route_anchor")
                parent_anchor = detail.get("parent_anchor")
                if (
                    not isinstance(detail_path, str)
                    or not isinstance(detail_anchor, str)
                    or detail_path not in scans
                    or detail_anchor not in scans[detail_path].anchors
                    or not isinstance(parent_anchor, str)
                    or not isinstance(path, str)
                    or path not in scans
                    or parent_anchor not in scans[path].anchors
                ):
                    issues.append(
                        Issue(
                            "authority_route",
                            AUTHORITY,
                            "delegated detail route or parent anchor is unavailable",
                        )
                    )

    conditional = registry["conditional"]
    assert isinstance(conditional, list)
    m24_routes = [
        route
        for route in route_objects
        if route.get("path") == M24
        and route.get("route_anchor") == "tg-m24-verification-runner"
    ]
    if conditional or len(m24_routes) != 1:
        issues.append(
            Issue(
                "authority_route",
                AUTHORITY,
                "mixed M24 owner or route anchor is unavailable",
            )
        )


def _m24_trusted_local_authority_sync(
    scans: dict[str, Scan], issues: list[Issue]
) -> None:
    """Keep only the explicit, user-adopted M24 Runner boundary as a canary."""
    scan = scans.get(M24)
    if scan is None:
        return
    prose = " ".join(
        " ".join(_semantic_prose(line).lower().split())
        for line in scan.semantic
    )
    required = (
        "trusted-local repository",
        "explicit opt-in",
        "untrusted or external target uses the m21 manual verification fallback",
        "fixed argv",
        "shell=false",
        "credential-excluding environment",
        "job object",
        "timeout",
        "bounded output",
        "private temporary",
        "cleanup",
        "raw stdout and stderr are transient and are never persisted",
        "only the closed runner outcome is persisted",
        "does not claim network isolation, hostile-code containment, or zero capability",
        "does not activate product code or a runner runtime",
        "candidate c, b-to-c, lpac, appcontainer, etw, and registry recovery are not current m24 gates",
        "runner-slice module registry and acyclic dependency graph",
        "cli.py may call only verification_runner_service.py",
        "repository and persistence modules never launch or import the process or os adapter",
        "closed typed bounded request plus its local boolean cancellation signal",
        "returns only the closed bounded sanitized result",
        "opens no canonical state",
        "verification_runner_service.py alone combines the process",
        "blocks on either uncertainty",
        "authorizes cleanup success or terminal persistence",
        "no raw output, argv, environment, credential, private path, exit code, or exception body crosses that persistence boundary",
        "logical request/result records add no serializer, ipc, worker, process, queue, pipe, socket, rpc, spool, supervisor, retry layer, schema, public cli, or product activation",
        "direct service-to-retired-os seam",
        "are physically absent after accepted r4a",
        "no archive or dormant copy",
        "dependency-pure, legacy-stable value-model foundation",
        "supplied by accepted r4v",
        "callback in the process adapter",
        "second cleanup-acceptance owner",
        "transitional nonconformance routed to r4b",
        "r2c repaired none of them and changed no r2a/r2b disposition or action selector",
    )
    if any(phrase not in prose for phrase in required):
        issues.append(
            Issue(
                "m24_trusted_local_authority_sync",
                M24,
                "trusted-local opt-in, manual fallback, process bounds, cleanup, privacy, or non-activation drifted",
            )
        )

    status_bindings = (
        ("tg-m24-r1", "tg-m24.r1", "accepted"),
        ("tg-m24-r2a", "tg-m24.r2a", "accepted"),
        ("tg-m24-r2b", "tg-m24.r2b", "accepted"),
        ("tg-m24-r2c", "tg-m24.r2c", "accepted"),
        ("tg-m24-r4a", "tg-m24.r4a", "accepted"),
        ("tg-m24-r4v", "tg-m24.r4v", "accepted"),
        ("tg-m24-r3a", "tg-m24.r3a", "accepted"),
        ("tg-m24-r3b", "tg-m24.r3b", "accepted"),
        ("tg-m24-r4b", "tg-m24.r4b", "accepted"),
        ("tg-m24-r5", "tg-m24.r5", "current"),
        ("tg-m24-2a", "tg-m24.2a", "inactive"),
        ("tg-m24-2b", "tg-m24.2b", "inactive"),
        ("tg-m24-2c", "tg-m24.2c", "inactive"),
        ("tg-m24-2", "tg-m24.2d", "inactive"),
        ("tg-m24-3", "tg-m24.3", "inactive"),
        ("tg-m24-4a", "tg-m24.4a", "inactive"),
        ("tg-m24-4b", "tg-m24.4b", "inactive"),
        ("tg-m24-4c", "tg-m24.4c", "inactive"),
        ("tg-m24-4", "tg-m24.4d", "inactive"),
        ("tg-m24-cp4", "tg-m24.cp4", "inactive"),
    )
    observed_statuses: list[tuple[str, str, str]] = []
    for anchor, unit, expected_status in status_bindings:
        bounds = _anchor_section(scan, anchor)
        headings = (
            ()
            if bounds is None
            else tuple(
                line
                for level, line, position in scan.headings
                if level == 2 and bounds[0] < position < bounds[1]
            )
        )
        expected_shape = (
            len(headings) == 2 and headings[1] == "## Expansion Boundary"
            if anchor == "tg-m24-cp4"
            else len(headings) == 1
        )
        heading = headings[0].lower() if headings else ""
        status_is_exact = (
            expected_shape
            and re.search(rf"\b{re.escape(unit)}\b", heading) is not None
            and _positive_status_term(heading, expected_status)
            and all(
                not _positive_status_term(heading, other)
                for other in ("accepted", "current", "inactive", "superseded")
                if other != expected_status
            )
        )
        if status_is_exact:
            observed_statuses.append((anchor, unit, expected_status))
    if tuple(observed_statuses) != status_bindings:
        issues.append(
            Issue(
                "m24_current_binding",
                M24,
                "M24 accepted predecessors, sole current R5 unit, or inactive successors drifted",
            )
        )


def _m24_r2c_architecture_boundary(
    scans: dict[str, Scan], issues: list[Issue]
) -> None:
    """Validate R2C structure without treating prose layout as authority."""

    def normalized(value: str) -> str:
        return " ".join(value.casefold().split())

    def patterns_match(value: str, patterns: tuple[str, ...]) -> bool:
        semantic = normalized(value)
        return all(re.search(pattern, semantic) is not None for pattern in patterns)

    def positive_patterns_match(value: str, patterns: tuple[str, ...]) -> bool:
        semantic = normalized(value)
        for pattern in patterns:
            matches = tuple(re.finditer(pattern, semantic))
            if not matches or any(
                _relation_occurrence_negated(semantic, match.start(), match.end())
                or re.search(
                    r"\b(?:not|no|never)\b"
                    r"(?:\s+[a-z0-9_/-]+){0,4}\s*$",
                    semantic[max(0, match.start() - 80) : match.start()],
                )
                for match in matches
            ):
                return False
        return True

    def section_blocks(heading: str) -> tuple[str, ...]:
        section = _section_bounds(design, heading)
        if section is None:
            return ()
        blocks: list[str] = []
        current: list[str] = []
        for position in range(section[0] + 1, section[1]):
            line = _semantic_prose(design.lines[position]).strip()
            if not line:
                if current:
                    blocks.append(normalized(" ".join(current)))
                    current = []
                continue
            current.append(line)
        if current:
            blocks.append(normalized(" ".join(current)))
        return tuple(blocks)

    def header_role(value: str) -> str | None:
        semantic = normalized(value)
        matches = tuple(
            role
            for role, patterns in R2C_TABLE_HEADER_ROLES.items()
            if all(re.search(pattern, semantic) is not None for pattern in patterns)
        )
        return matches[0] if len(matches) == 1 else None

    def comma_tokens(value: str, pattern: str) -> tuple[str, ...] | None:
        tokens = tuple(part.strip() for part in value.replace("`", "").split(","))
        return tokens if tokens and all(re.fullmatch(pattern, token) for token in tokens) else None

    def acyclic(edges: set[tuple[str, str]]) -> bool:
        visiting: set[str] = set()
        visited: set[str] = set()
        graph = {
            layer: {target for source, target in edges if source == layer}
            for layer in R2C_LAYER_MODULES
        }

        def visit(layer: str) -> bool:
            if layer in visiting:
                return False
            if layer in visited:
                return True
            visiting.add(layer)
            if not all(visit(target) for target in graph[layer]):
                return False
            visiting.remove(layer)
            visited.add(layer)
            return True

        return all(visit(layer) for layer in graph)

    design = scans[DESIGN]
    bounds = _section_bounds(design, R2C_BOUNDARY_HEADING)
    table = _sequence_table(design, R2C_BOUNDARY_TABLE_HEADING)
    valid = (
        bounds is not None
        and design.anchors.get("tg-m24-r2c-runner-architecture-boundary", len(design.lines))
        < bounds[0]
        and table is not None
        and len(table) == len(R2C_LAYER_MODULES) + 2
    )

    observed_edges: set[tuple[str, str]] = set()
    column_indexes: dict[str, int] = {}
    if valid and table is not None:
        header = _cells(table[0])
        separator = _cells(table[1])
        roles = tuple(header_role(cell) for cell in header)
        valid = (
            len(header) == len(R2C_TABLE_HEADER_ROLES)
            and None not in roles
            and set(roles) == set(R2C_TABLE_HEADER_ROLES)
            and len(separator) == len(header)
            and all(re.fullmatch(r":?-{3,}:?", cell) for cell in separator)
        )
        if valid:
            column_indexes = {
                role: index for index, role in enumerate(roles) if role is not None
            }

    observed_layers: set[str] = set()
    observed_modules: list[str] = []
    cleanup_responsibility_layers: set[str] = set()
    if valid and table is not None:
        for row in table[2:]:
            cells = _cells(row)
            if len(cells) != len(R2C_TABLE_HEADER_ROLES):
                valid = False
                break
            layer = cells[column_indexes["layer"]].strip().strip("`")
            if re.fullmatch(r"[a-z][a-z0-9_]*", layer) is None:
                valid = False
                break
            modules = comma_tokens(
                cells[column_indexes["modules"]],
                r"_?[A-Za-z][A-Za-z0-9_]*\.py",
            )
            import_cell = cells[column_indexes["imports"]]
            imports = (
                ()
                if normalized(import_cell).strip("`") == "none"
                else comma_tokens(import_cell, r"[a-z][a-z0-9_]*")
            )
            routes = tuple(
                re.findall(
                    r"(?<![A-Za-z0-9])(?:TG-M24\.)?"
                    r"(R[0-9]+[A-Z]?|CP[0-9]+|[0-9]+[A-Z])"
                    r"(?![A-Za-z0-9])",
                    cells[column_indexes["route"]],
                )
            )
            if (
                layer not in R2C_LAYER_MODULES
                or layer in observed_layers
                or modules is None
                or imports is None
                or len(modules) != len(set(modules))
                or set(modules) != set(R2C_LAYER_MODULES[layer])
                or len(imports) != len(set(imports))
                or set(imports) != set(R2C_LAYER_IMPORTS[layer])
                or len(routes) != len(set(routes))
                or set(routes) != set(R2C_LAYER_ROUTE_UNITS[layer])
                or not positive_patterns_match(
                    cells[column_indexes["responsibility"]],
                    R2C_LAYER_RESPONSIBILITY_PATTERNS[layer],
                )
                or not patterns_match(
                    cells[column_indexes["forbidden"]],
                    R2C_LAYER_FORBIDDEN_PATTERNS[layer],
                )
            ):
                valid = False
                break
            observed_layers.add(layer)
            observed_modules.extend(modules)
            observed_edges.update((layer, target) for target in imports)
            if re.search(
                r"\bcleanup acceptance\b",
                normalized(cells[column_indexes["responsibility"]]),
            ):
                cleanup_responsibility_layers.add(layer)
        valid = valid and observed_layers == set(R2C_LAYER_MODULES) and len(
            observed_modules
        ) == len(set(observed_modules)) and cleanup_responsibility_layers == {"service"}

    text_blocks = (
        ()
        if bounds is None
        else tuple(
            tuple(design.lines[start + 1 : end])
            for info, start, end in design.fences
            if bounds[0] < start < end < bounds[1] and info.strip() == "text"
        )
    )
    layer_names = "|".join(re.escape(layer) for layer in R2C_LAYER_MODULES)
    edge_pattern = re.compile(
        rf"(?<![a-z0-9_])(?P<source>{layer_names})\s*->\s*"
        rf"(?P<target>{layer_names})(?![a-z0-9_])"
    )
    edge_blocks: list[tuple[tuple[str, str], ...]] = []
    record_blocks: list[dict[str, tuple[str, ...]]] = []
    bound_blocks: list[dict[str, tuple[str, str]]] = []
    record_names = "|".join(re.escape(name) for name in R2C_RECORD_MEMBERS)
    record_pattern = re.compile(
        rf"\b(?P<name>{record_names})\s*=\s*(?P<body>.*?)"
        rf"(?=(?:{record_names})\s*=|$)"
    )
    control_names = "|".join(
        re.escape(name) for name in sorted(R2C_BOUND_CONTROLS, key=len, reverse=True)
    )
    control_pattern = re.compile(
        rf"(?<![A-Za-z0-9_])(?P<key>{control_names})\s*"
        rf"(?P<operator><=|=)\s*"
    )
    classified_blocks = 0
    for block in text_blocks:
        joined = "\n".join(block).strip()
        collapsed = " ".join(joined.split())
        edge_matches = tuple(edge_pattern.finditer(joined))
        if edge_matches and not edge_pattern.sub("", joined).strip():
            edge_blocks.append(
                tuple(
                    (match.group("source"), match.group("target"))
                    for match in edge_matches
                )
            )
            classified_blocks += 1
            continue
        if re.search(rf"\b(?:{record_names})\s*=", collapsed):
            matches = tuple(record_pattern.finditer(collapsed))
            records: dict[str, tuple[str, ...]] = {}
            records_valid = (
                bool(matches)
                and matches[0].start() == 0
                and matches[-1].end() == len(collapsed)
            )
            for match in matches:
                raw_members = tuple(
                    member.strip() for member in match.group("body").split(",")
                )
                members = tuple(
                    member for member in raw_members if re.fullmatch(r"[a-z_]+", member)
                )
                if (
                    len(members) != len(raw_members)
                    or match.group("name") in records
                ):
                    records_valid = False
                records[match.group("name")] = members
            valid = valid and records_valid
            record_blocks.append(records)
            classified_blocks += 1
            continue
        header = re.match(r"^RunnerProcessBoundsV1\s*:\s*", collapsed)
        if header is not None:
            body = collapsed[header.end() :]
            matches = tuple(control_pattern.finditer(body))
            controls: dict[str, tuple[str, str]] = {}
            controls_valid = bool(matches) and matches[0].start() == 0
            for index, match in enumerate(matches):
                end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
                value = " ".join(body[match.end() : end].strip(" ;").split())
                key = match.group("key")
                if not value or key in controls:
                    controls_valid = False
                controls[key] = (match.group("operator"), value)
            valid = valid and controls_valid
            bound_blocks.append(controls)
            classified_blocks += 1
            continue
        valid = False

    expected_edges = {
        (layer, target)
        for layer, targets in R2C_LAYER_IMPORTS.items()
        for target in targets
    }
    explicit_edges = set(edge_blocks[0]) if len(edge_blocks) == 1 else set()
    valid = (
        valid
        and classified_blocks == len(text_blocks) == 3
        and len(edge_blocks) == 1
        and len(edge_blocks[0]) == len(explicit_edges)
        and explicit_edges == expected_edges == observed_edges
        and acyclic(explicit_edges)
        and len(record_blocks) == 1
        and set(record_blocks[0]) == set(R2C_RECORD_MEMBERS)
        and all(
            len(record_blocks[0][name]) == len(set(record_blocks[0][name]))
            and set(record_blocks[0][name]) == set(members)
            for name, members in R2C_RECORD_MEMBERS.items()
        )
        and len(bound_blocks) == 1
    )
    expected_controls = {
        key: (operator, " ".join(value.split()))
        for key, (operator, value) in R2C_BOUND_CONTROLS.items()
    }
    if bound_blocks:
        valid = valid and bound_blocks[0] == expected_controls
    else:
        valid = False

    excluded: set[int] = set()
    if bounds is not None:
        for _info, start, end in design.fences:
            if bounds[0] < start < end < bounds[1]:
                excluded.update(range(start, end + 1))
        table_range = _section_table_range(design, R2C_BOUNDARY_TABLE_HEADING)
        if table_range is not None:
            excluded.update(range(table_range[0], table_range[1]))
    prose = normalized(
        " ".join(
            _semantic_prose(design.lines[position])
            for position in range(bounds[0] + 1, bounds[1])
            if position not in excluded
        )
    ) if bounds is not None else ""
    cleanup_blocks = section_blocks(
        "### Cleanup Acceptance, Privacy, And Non-Activation"
    )
    cleanup_owner_block = next(
        (
            block
            for block in cleanup_blocks
            if re.search(r"single cleanup-\s*acceptance owner", block)
        ),
        "",
    )
    privacy_block = next(
        (block for block in cleanup_blocks if "raw output" in block),
        "",
    )
    architecture_absence_block = next(
        (block for block in cleanup_blocks if "define no serializer" in block),
        "",
    )
    activation_block = next(
        (block for block in cleanup_blocks if "r2c adds no ipc" in block),
        "",
    )
    cleanup_inputs = (
        "verification_runner_service.py",
        "process-tree zero",
        "handle closure",
        "output discard",
        "private-tree absence",
        "terminal persistence",
    )
    privacy_inputs = (
        "raw output",
        "argv",
        "environment",
        "credentials",
        "private paths",
        "exit codes",
        "exception bodies",
    )
    architecture_absence_elements = (
        "serializer",
        "file spool",
        "queue",
        "pipe",
        "socket",
        "rpc",
        "worker",
        "daemon",
        "subprocess wrapper",
        "supervisor",
        "heartbeat",
        "retry protocol",
        "secondary state store",
        "second database connection",
    )
    activation_elements = (
        "ipc",
        "process",
        "schema",
        "public cli",
        "skill trigger",
        "completion gate",
        "product behavior",
    )
    cleanup_owner_relation = re.sub(r"-\s+", "-", cleanup_owner_block)
    focused_relations_valid = (
        all(term in cleanup_owner_block for term in cleanup_inputs)
        and all(
            _positive_relation_term(cleanup_owner_relation, term)
            for term in (
                "single cleanup-acceptance owner",
                "alone combines",
                "alone authorizes",
            )
        )
        and all(term in privacy_block for term in privacy_inputs)
        and _positive_relation_term(privacy_block, "remain transient")
        and _positive_relation_term(privacy_block, "never stored")
        and all(
            term in architecture_absence_block
            for term in architecture_absence_elements
        )
        and _positive_relation_term(
            architecture_absence_block,
            "define no serializer",
        )
        and all(term in activation_block for term in activation_elements)
        and _positive_relation_term(activation_block, "r2c adds no ipc")
    )
    semantic_relations = (
        r"member sets are closed",
        r"bounded sanitized structural values.{0,80}not arbitrary text",
        r"r2c gates only.{0,180}result_code.{0,120}bindings",
        r"does not define a concrete code taxonomy.{0,100}pairing",
        r"2b owns.{0,100}membership.{0,80}pairing.{0,180}2c owns.{0,140}mapping.{0,80}projection",
        r"parent service accepts no arbitrary adapter text.{0,160}persistence owner",
        r"does not alter.{0,100}existing closed durable outcome",
        r"observable payload.{0,80}one boolean.{0,120}no callback.{0,100}business gate",
        r"trusted code.{0,100}not a hostile-code sandbox.{0,100}network isolation",
        r"not qualification gates",
        r"accepted r4a physical-deletion scope.{0,100}physically absent.{0,100}not architecture nodes.{0,100}r4b scope",
        r"2a/2b/2c own.{0,220}r2c repairs or activates none",
    )
    design_prose = normalized(
        " ".join(_semantic_prose(line) for line in design.semantic)
    )
    standard_lane_relations = (
        r"accepted r4a removed.{0,100}tg-m24\.1a lpac module.{0,100}mandatory native fixture.{0,120}dedicated retired-route tests",
        r"standard test partition.{0,100}only the three base lanes.{0,80}fast.{0,80}integration.{0,80}release",
    )
    valid = (
        valid
        and focused_relations_valid
        and all(re.search(pattern, prose) is not None for pattern in semantic_relations)
        and all(
            re.search(pattern, design_prose) is not None
            for pattern in standard_lane_relations
        )
    )
    if not valid:
        issues.append(
            Issue(
                "m24_r2c_architecture_boundary",
                DESIGN,
                "R2C layer registry, DAG, typed values, cleanup owner, privacy, routing, or non-activation drifted",
            )
        )


def _anchor_section(scan: Scan, anchor: str) -> tuple[int, int] | None:
    start = scan.anchors.get(anchor)
    if start is None:
        return None
    later = [position for position in scan.anchors.values() if position > start]
    return start, min(later) if later else len(scan.lines)


def _documentation_sequence(scans: dict[str, Scan], issues: list[Issue]) -> None:
    plan = scans["plan.md"]
    required = ("tg-doc-sequence",) + tuple(unit.anchor for unit in DOCUMENTATION_UNITS)
    if any(anchor not in plan.anchors for anchor in required):
        issues.append(
            Issue(
                "sequence_contract",
                "plan.md",
                "documentation sequence anchors are incomplete",
            )
        )
        return
    positions = tuple(plan.anchors[anchor] for anchor in required)
    if positions != tuple(sorted(positions)) or len(set(positions)) != len(positions):
        issues.append(
            Issue(
                "sequence_contract",
                "plan.md",
                "documentation sequence anchors are out of order",
            )
        )

    for unit in DOCUMENTATION_UNITS:
        bounds = _anchor_section(plan, unit.anchor)
        assert bounds is not None
        next_heading = next(
            (
                line
                for _level, line, position in plan.headings
                if bounds[0] < position < bounds[1]
            ),
            "",
        )
        heading_lower = next_heading.lower()
        valid = (
            re.search(rf"\b{re.escape(unit.unit)}\b", next_heading) is not None
            and _positive_status_term(heading_lower, unit.heading_status)
        )
        if not valid:
            issues.append(
                Issue(
                    "sequence_contract",
                    "plan.md",
                    f"{unit.unit} accepted/inactive owner heading drifted",
                )
            )


def _directly_negated(text: str, position: int) -> bool:
    prefix = text[max(0, position - 32) : position].lower()
    return bool(
        re.search(
            r"(?:\b(?:not|no|never)\s+(?:(?:the|an?)\s+)?|"
            r"\bno\s+longer\s+|\b(?:formerly|previously)\s+|\bnon[-\s]*)$",
            prefix,
        )
    )


def _positive_status_term(text: str, term: str) -> bool:
    pattern = r"\bpredecessors?\b" if term == "predecessor" else rf"\b{re.escape(term)}\b"
    matches = tuple(re.finditer(pattern, text))
    return bool(matches) and all(
        not _directly_negated(text, match.start()) for match in matches
    )


def _negative_current_relation(text: str) -> bool:
    return bool(
        re.search(
            r"\bno\s+(?:(?:tg-[a-z0-9.]+\s+)?(?:execution\s+)?)?"
            r"units?\s+(?:is|are)\s+current\b"
            r"|\b(?:(?:tg-[a-z0-9.]+\s+)?(?:execution\s+)?)?units?\s+"
            r"(?:is|are)\s+(?:not|never|no\s+longer)\s+current\b"
            r"|\bthere\s+(?:is|are)\s+no\s+current\s+(?:unit|task)s?\b",
            text,
        )
    )


def _has_current_status_contradiction(text: str) -> bool:
    return any(
        not _directly_negated(text, match.start())
        for pattern in (
            CURRENT_STATUS_CLAIM,
            M24_R3B_CURRENT_CLAIM,
            M24_R4B_CURRENT_CLAIM,
            M24_R4B_INACTIVE_CLAIM,
            M24_R5_INACTIVE_CLAIM,
        )
        for match in pattern.finditer(text)
    )


def _has_positive_unit_current_relation(text: str) -> bool:
    return any(
        not _directly_negated(text, match.start())
        for match in UNIT_CURRENT_CLAIM.finditer(text)
    )


def _has_live_review_target(text: str) -> bool:
    for raw_line in text.splitlines():
        line = _markdown_container_content(raw_line).strip()
        key_value = LIVE_REVIEW_TARGET_KV.fullmatch(line)
        if key_value and _is_live_review_target_value(
            key_value.group("field"), key_value.group("value")
        ):
            return True
        if line.startswith("|") and line.endswith("|"):
            cells = _cells(line)
            if (
                len(cells) >= 2
                and cells[0].strip(chr(96)).lower() in LIVE_REVIEW_TARGET_FIELDS
                and _is_live_review_target_value(cells[0], cells[1])
            ):
                return True
    return False


def _has_live_status(text: str) -> bool:
    for raw_line in text.splitlines():
        line = _markdown_container_content(raw_line).strip()
        key_value = LIVE_STATUS_KV.fullmatch(line)
        if key_value and _is_live_status_value(
            key_value.group("field"), key_value.group("value")
        ):
            return True
        if line.startswith("|") and line.endswith("|"):
            cells = _cells(line)
            if (
                len(cells) >= 2
                and cells[0].strip(chr(96)).lower() in LIVE_STATUS_FIELDS
                and _is_live_status_value(cells[0], cells[1])
            ):
                return True
    return False


def _has_unit_live_state(text: str) -> bool:
    status_values = "|".join(sorted(TASK_STATUS_VALUES))
    task_pattern = r"TG-[A-Z0-9.]+"
    unit_status_patterns = (
        re.compile(
            rf"(?i)\b{task_pattern}\b[^\n]{{0,40}}\b"
            rf"(?:status|current_status)\s*(?::|is|=)\s*"
            rf"(?:{status_values})\b"
        ),
        re.compile(
            rf"(?i)\b(?:the\s+)?(?:status|current\s+status)\s+of\s+"
            rf"{task_pattern}\b\s*(?:is|:|=)\s*(?:{status_values})\b"
        ),
        re.compile(
            rf"(?i)\b{task_pattern}\b\s*(?:is|:|=)\s*"
            rf"(?:{status_values})\b"
        ),
        re.compile(
            rf"(?i)\b{task_pattern}\b\s*(?:is|:|=)\s*"
            rf"(?:the\s+)?(?:current|next)\b"
        ),
    )
    target_pattern = re.compile(
        r"(?i)\b(?P<field>review_target_kind|review_target_value|"
        r"review_target_base_revision|review_target_generation)\s*(?::|=)\s*"
        r"(?P<value>[^\s,;|]+)"
    )
    table_header: tuple[str, ...] | None = None
    table_ready = False
    for raw_line in text.splitlines():
        line = _markdown_container_content(raw_line).strip()
        if any(pattern.search(line) for pattern in unit_status_patterns):
            return True
        for target in target_pattern.finditer(line):
            if TASK_ID.search(line[: target.start()]) and _is_live_review_target_value(
                target.group("field"), target.group("value").rstrip(".")
            ):
                return True
        if line.startswith("|") and line.endswith("|"):
            cells = tuple(
                " ".join(_semantic_prose(cell).lower().split())
                for cell in _cells(line)
            )
            if any(TASK_ID.fullmatch(cell) for cell in cells) and any(
                cell in TASK_STATUS_VALUES for cell in cells
            ):
                return True
            if (
                len(cells) >= 2
                and cells[0] in {"current task", "current unit", "next task", "next unit"}
                and TASK_ID.fullmatch(cells[1])
            ):
                return True
            if table_header is None:
                table_header = cells
                table_ready = False
                continue
            if (
                not table_ready
                and len(cells) == len(table_header)
                and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells)
            ):
                table_ready = True
                continue
            if table_ready and len(cells) == len(table_header):
                task_columns = tuple(
                    index
                    for index, header in enumerate(table_header)
                    if header
                    in {
                        "task",
                        "task id",
                        "task_id",
                        "unit",
                        "unit id",
                        "unit_id",
                    }
                )
                target_columns = tuple(
                    (index, header)
                    for index, header in enumerate(table_header)
                    if header in LIVE_REVIEW_TARGET_FIELDS
                )
                if any(
                    TASK_ID.fullmatch(cells[task_index])
                    and _is_live_review_target_value(
                        field, cells[target_index]
                    )
                    for task_index in task_columns
                    for target_index, field in target_columns
                ):
                    return True
                continue
            table_header = cells
            table_ready = False
            continue
        table_header = None
        table_ready = False
    return False


def _is_live_status_value(field: str, value: str) -> bool:
    normalized_field = field.strip().strip(chr(96)).lower()
    normalized_value = value.strip().strip(chr(96)).strip().lower()
    if normalized_field in {"status", "current_status"}:
        return normalized_value in {
            "ready",
            "in_progress",
            "review_pending",
            "blocked",
            "paused",
            "done",
        }
    if normalized_field in {"blocked_reason", "pause_reason"}:
        return bool(normalized_value) and not bool(
            re.fullmatch(
                r"(?:string|text|null|nullable|required|optional|none|n/a)",
                normalized_value,
            )
        )
    if normalized_field == "completed_at":
        return bool(
            re.fullmatch(
                r"[12][0-9]{3}-[0-9]{2}-[0-9]{2}t"
                r"[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?z",
                normalized_value,
            )
        )
    if normalized_field == "completion_commit_hash":
        return bool(re.fullmatch(r"[0-9a-f]{40,64}", normalized_value))
    return False


def _is_live_review_target_value(field: str, value: str) -> bool:
    normalized_field = field.strip().strip(chr(96)).lower()
    normalized_value = value.strip().strip(chr(96)).strip().lower()
    if normalized_value == "null":
        return True
    schema_descriptor = bool(
        re.fullmatch(
            r"(?:str(?:ing)?|text|integer|number|hash|digest|revision|value|"
            r"kind|optional|required|nullable|none|n/a)"
            r"(?:\s*[|/]\s*(?:str(?:ing)?|text|integer|number|hash|digest|"
            r"revision|value|kind|optional|required|nullable|none|n/a))*",
            normalized_value,
        )
    )
    if schema_descriptor:
        return False
    if normalized_field == "review_target_generation":
        return bool(re.fullmatch(r"[0-9]+", normalized_value))
    if normalized_field == "review_target_kind":
        return bool(normalized_value)
    if normalized_field == "review_target_base_revision":
        return bool(normalized_value)
    if normalized_field == "review_target_value":
        return bool(normalized_value)
    return False


def _normalize_quote_block(lines: tuple[str, ...] | list[str]) -> str:
    payloads = []
    for line in lines:
        payload = line[1:] if line.startswith(">") else line
        if payload.startswith(" "):
            payload = payload[1:]
        payloads.append(payload)
    return " ".join("\n".join(payloads).split()).lower()


def _roles(
    scans: dict[str, Scan],
    registry: dict[str, object] | None,
    issues: list[Issue],
) -> None:
    for relative, scan in scans.items():
        h1 = [(line, position) for level, line, position in scan.headings if level == 1]
        if len(h1) != 1 or not scan.headings or scan.headings[0][0] != 1:
            issues.append(
                Issue(
                    "document_role",
                    relative,
                    "document must have one visible top-level owner heading",
                )
            )
        else:
            title = h1[0][0].lower()
            required_title = ROLE_TITLE_TOKENS.get(relative, ())
            if not all(
                _positive_status_term(title, token) for token in required_title
            ):
                issues.append(
                    Issue(
                        "document_role",
                        relative,
                        "top-level heading contradicts the registered document role",
                    )
                )

        required_banner = ROLE_BANNER_STATUS.get(relative)
        if required_banner is not None:
            banner = (
                ""
                if not scan.quotes
                else _normalize_quote_block(scan.quotes[0])
            )
            positive_terms, requires_no_current = required_banner
            valid_banner = all(
                _positive_status_term(banner, term) for term in positive_terms
            ) and (
                not requires_no_current or _negative_current_relation(banner)
            ) and not _has_positive_unit_current_relation(banner)
            if not valid_banner:
                issues.append(
                    Issue(
                        "document_role",
                        relative,
                        "first structural role block does not assert the registered authority status",
                    )
                )

        semantic = "\n".join(scan.semantic)
        semantic_prose = _semantic_prose(semantic)
        normalized_semantic = " ".join(semantic_prose.split())
        if _has_current_status_contradiction(normalized_semantic):
            issues.append(
                Issue(
                    "document_role",
                    relative,
                    "prose contradicts the registered authority status",
                )
            )
        if (
            VOLATILE_ID.search(semantic_prose)
            or _has_live_status(semantic_prose)
            or LIVE_EXECUTION.search(semantic_prose)
            or LIVE_EXECUTION_REVERSE.search(semantic_prose)
            or _has_live_review_target(semantic_prose)
            or _has_unit_live_state(semantic_prose)
        ):
            issues.append(
                Issue(
                    "volatile_state",
                    relative,
                    "Git documentation must not mirror live Task evidence or status",
                )
            )

    if registry is None:
        return
    current = registry["current"]
    mixed = registry["mixed_execution"]
    conditional = registry["conditional"]
    history = registry["history_index"]
    assert isinstance(current, list)
    assert isinstance(mixed, list)
    assert isinstance(conditional, list)
    assert isinstance(history, str)
    role_paths = list(current)
    role_paths.extend(
        item["path"] for item in mixed if isinstance(item, dict) and "path" in item
    )
    role_paths.extend(conditional)
    role_paths.append(history)
    if (
        len(role_paths) != len(set(role_paths))
        or history in current
        or history in conditional
        or any(
            isinstance(item, dict) and item.get("path") == history for item in mixed
        )
    ):
        issues.append(
            Issue(
                "document_role",
                AUTHORITY,
                "current, execution, conditional, and history owners overlap",
            )
        )


def _valid_history_declaration(block: str, *, index: bool) -> bool:
    lowered = _normalize_quote_block(block.splitlines())
    declared = _positive_status_term(lowered, "non-authoritative") or bool(
        index
        and re.search(r"\bnot\s+(?:the\s+)?current\s+authority\b", lowered)
    )
    conflicting = False
    for match in re.finditer(r"\bauthoritative\b", lowered):
        if not _directly_negated(lowered, match.start()):
            conflicting = True
            break
    for match in re.finditer(r"\b(?:binding|current|active)\s+authority\b", lowered):
        if _directly_negated(lowered, match.start()):
            continue
        relation = match.group(0)
        if relation == "current authority":
            suffix = lowered[match.end() : match.end() + 48]
            prefix = lowered[max(0, match.start() - 8) : match.start()]
            routed_replacement = bool(
                re.match(r"\s+(?:is|remains)\s+(?:in\s+)?\[", suffix)
                or prefix.endswith("for ")
            )
            if routed_replacement:
                continue
        conflicting = True
        break
    return declared and not conflicting


def _visible_quote_warning(
    lines: list[str], position: int, *, index: bool
) -> bool:
    block: list[str] = []
    inline_carry = ""
    html_comment = False
    fence_marker = ""
    fence_container = FenceContainer(())
    fence_poisoned = False
    raw_html_tag = ""
    while position < len(lines) and lines[position].startswith(">"):
        line = lines[position]
        payload = line[2:] if line.startswith("> ") else line[1:]
        if fence_marker:
            container_content = _fence_container_content(line, fence_container)
            if container_content is None:
                fence_poisoned = True
            elif not fence_poisoned and _fence_closes(
                container_content, fence_marker
            ):
                fence_marker = ""
                fence_container = FenceContainer(())
                fence_poisoned = False
            position += 1
            continue
        inert_html, raw_html_tag = _html_block_step(payload, raw_html_tag)
        if inert_html:
            position += 1
            continue
        indented_code = payload.startswith("\t") or payload.startswith("    ")
        if not (html_comment or inline_carry):
            opener = _fence_opener_with_container(line)
            if opener is not None:
                fence_marker, _info, fence_container = opener
                fence_poisoned = False
                position += 1
                continue
        masked, inline_carry, html_comment = _mask_markup(
            payload, inline_carry, html_comment
        )
        if not indented_code:
            block.append("> " + masked)
        position += 1
    return bool(block) and _valid_history_declaration("\n".join(block), index=index)


def _first_structural_warning(text: str, *, index: bool = False) -> bool:
    lines = text.replace("\r\n", "\n").splitlines()
    position = 0
    while position < len(lines) and not lines[position].strip():
        position += 1
    if position >= len(lines):
        return False
    first = lines[position].lstrip("\ufeff")
    masked_first, _carry, _comment = _mask_markup(first)
    heading = ATX_HEADING.fullmatch(masked_first)
    if heading and len(heading.group(1)) == 1:
        body = re.sub(r"[ \t]+#+[ \t]*$", "", heading.group(2)).strip()
        if _valid_history_declaration(body, index=index):
            return True
        position += 1
        while position < len(lines) and not lines[position].strip():
            position += 1
    if position >= len(lines) or not lines[position].startswith(">"):
        return False
    return _visible_quote_warning(lines, position, index=index)


def _history(root: Path, index: Scan, issues: list[Issue]) -> None:
    history_root = root / "docs" / "history"
    captures: list[Path] = []
    try:
        candidates = sorted(history_root.rglob("*"))
    except OSError:
        candidates = []
        issues.append(
            Issue(
                "history_file",
                "docs/history",
                "history directory could not be enumerated safely",
            )
        )
    for path in candidates:
        relative = path.relative_to(root).as_posix()
        if _is_link_like(path):
            issues.append(
                Issue(
                    "history_file",
                    relative,
                    "history must not contain links or reparse points",
                )
            )
            continue
        if path.is_file():
            if path == history_root / "README.md":
                continue
            if path.suffix != ".md":
                issues.append(
                    Issue(
                        "history_file",
                        relative,
                        "history regular files must use lowercase .md",
                    )
                )
                continue
            captures.append(path)

    counts = {path.relative_to(root).as_posix(): 0 for path in captures}
    for link in index.links:
        resolved = _resolve(root, HISTORY_INDEX, link.target)
        if resolved is not None and resolved[0] in counts:
            counts[resolved[0]] += 1

    for relative, count in counts.items():
        if count != 1:
            issues.append(
                Issue(
                    "history_index",
                    relative,
                    "historical Markdown must be indexed exactly once",
                )
            )
        path = root.joinpath(*relative.split("/"))
        try:
            text = path.read_bytes().decode("utf-8")
        except (OSError, UnicodeError):
            issues.append(
                Issue(
                    "history_file",
                    relative,
                    "historical Markdown must be readable UTF-8",
                )
            )
            continue
        if not _first_structural_warning(text):
            issues.append(
                Issue(
                    "history_banner",
                    relative,
                    "first structural role block must declare non-authoritative history",
                )
            )

    if not _first_structural_warning("\n".join(index.lines) + "\n", index=True):
        issues.append(
            Issue(
                "history_banner",
                HISTORY_INDEX,
                "history index must begin with a non-authority warning",
            )
        )


def _ignore_glob_regex(rule: str) -> re.Pattern[str]:
    anchored = rule.startswith("/") or "/" in rule
    pattern = rule.lstrip("/")
    directory_only = pattern.endswith("/")
    if directory_only:
        pattern = pattern[:-1]

    translated: list[str] = []
    position = 0
    while position < len(pattern):
        character = pattern[position]
        if character == "*":
            if position + 1 < len(pattern) and pattern[position + 1] == "*":
                position += 2
                if position < len(pattern) and pattern[position] == "/":
                    translated.append("(?:.*/)?")
                    position += 1
                else:
                    translated.append(".*")
                continue
            translated.append("[^/]*")
        elif character == "?":
            translated.append("[^/]")
        elif character == "[":
            end = position + 1
            if end < len(pattern) and pattern[end] in ("!", "^"):
                end += 1
            if end < len(pattern) and pattern[end] == "]":
                end += 1
            while end < len(pattern) and pattern[end] != "]":
                end += 1
            if end >= len(pattern):
                translated.append(r"\[")
            else:
                content = pattern[position + 1 : end]
                if content.startswith("!"):
                    content = "^" + content[1:]
                elif content.startswith("^"):
                    content = "\\" + content
                translated.append("[" + content.replace("\\", r"\\") + "]")
                position = end
        else:
            translated.append(re.escape(character))
        position += 1

    prefix = "^" if anchored else r"^(?:.*/)?"
    suffix = r"(?:/.*)?$" if directory_only else "$"
    return re.compile(prefix + "".join(translated) + suffix)


def _ignore_rule_matches(path: str, rule: str) -> bool:
    return bool(_ignore_glob_regex(rule).fullmatch(path))


def _gitignore_consumer_rule(rule: str) -> str:
    while rule.endswith(" "):
        backslashes = 0
        position = len(rule) - 2
        while position >= 0 and rule[position] == "\\":
            backslashes += 1
            position -= 1
        if backslashes % 2:
            break
        rule = rule[:-1]
    return rule


def _search_policy(root: Path, text: str, issues: list[Issue]) -> None:
    rules = [
        line
        for line in text.splitlines()
        if line and not line.startswith("#")
    ]
    history_root = root / "docs" / "history"
    try:
        candidates = {"docs/history"}
        candidates.update(
            path.relative_to(root).as_posix() for path in history_root.rglob("*")
        )
    except OSError:
        issues.append(
            Issue(
                "search_policy",
                ".ignore",
                "history search exclusion could not be inspected safely",
            )
        )
        return

    exact_seen = False
    effective = False
    for rule in rules:
        if rule == "/docs/history/":
            exact_seen = True
            effective = True
            continue
        consumer_rule = _gitignore_consumer_rule(rule)
        if not exact_seen or not consumer_rule.startswith("!"):
            continue
        pattern = consumer_rule[1:]
        if any(_ignore_rule_matches(candidate, pattern) for candidate in candidates):
            effective = False
    if not exact_seen or not effective:
        issues.append(
            Issue(
                "search_policy",
                ".ignore",
                "ordinary repository search must exclude docs/history",
            )
        )


def _metrics(raw_docs: dict[str, tuple[bytes, str]]) -> tuple[Metric, ...]:
    return tuple(
        Metric(relative, len(raw_docs[relative][1].splitlines()), len(raw_docs[relative][0]))
        for relative in METRIC_DOCS
        if relative in raw_docs
    )


def check_document_contract(repo_root: str | os.PathLike[str]) -> Result:
    root = Path(repo_root).resolve()
    issues: list[Issue] = []
    raw_docs: dict[str, tuple[bytes, str]] = {}
    scans: dict[str, Scan] = {}

    for relative in METRIC_DOCS:
        document = _read(root, relative, issues)
        if document is not None:
            raw_docs[relative] = document
            scans[relative] = _scan(relative, document[1], issues)

    ignore = _read(root, ".ignore", issues)
    if ignore is not None:
        _search_policy(root, ignore[1], issues)

    registry: dict[str, object] | None = None
    if all(relative in scans for relative in CANONICAL_DOCS):
        registry = _registry(scans[AUTHORITY], issues)
        _links_and_routes(root, scans, issues)
        _sequences(scans, issues)
        _bounded_reading_controls(scans, issues)
        _m24_trusted_local_authority_sync(scans, issues)
        _m24_r2c_architecture_boundary(scans, issues)
        _documentation_sequence(scans, issues)
        if registry is not None:
            _registry_routes(scans, registry, issues)
        _history(root, scans[HISTORY_INDEX], issues)

    _roles(scans, registry, issues)
    return Result(_metrics(raw_docs), tuple(sorted(set(issues))))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check repository documentation authority offline and read-only."
    )
    parser.add_argument(
        "--repo", default=str(DEFAULT_REPO_ROOT), help="source repository root"
    )
    parser.add_argument("--json", action="store_true", help="emit deterministic JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = check_document_contract(args.repo)
    except Exception:
        result = Result(
            (),
            (
                Issue(
                    "checker_internal_error",
                    "document_contract",
                    "document checker could not complete safely",
                ),
            ),
        )
    if args.json:
        print(json.dumps(result.to_data(), ensure_ascii=False, sort_keys=True))
    elif result.ok:
        print(f"document contract: PASS ({len(result.metrics)} documents measured)")
    else:
        print(f"document contract: FAIL ({len(result.issues)} issue(s))")
        for issue in result.issues:
            print(f"- {issue.code}: {issue.subject}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
