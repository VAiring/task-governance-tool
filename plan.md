# task-governance-tool Initial Plan

Status: formal MVP specification, design, and implementation roadmap
introduced; implementation not started.

This document captures early ideas for a reusable task-governance-tool Codex
skill/tooling project. Product behavior is now governed by
`docs/specification.md`, implementation structure is governed by
`docs/design.md`, and implementation order is governed by
`docs/implementation-roadmap.md`. This file remains the decision log and
open-issue holding area. The previous working name `task-governance` is
superseded by `task-governance-tool`.

## Goal

Build a reusable local-first assistant layer that helps Codex and the user run
high-quality project work with explicit task boundaries, governing-doc rereads,
verification gates, review gates, and durable but non-authoritative local
history.

The key product promise:

```text
Given a project, identify its governance rules, plan the next bounded execution
unit, run or suggest the right verification gates, generate review requests,
and record sanitized task history without becoming the project's source of
truth.
```

## Initial Product Shape

- `task-governance-tool` Codex skill
  - concise `SKILL.md`
  - one-level `references/` for deeper workflow guidance
  - `scripts/` for deterministic helper commands
- Python CLI named `taskgov`
  - stdlib-first implementation
  - SQLite through `sqlite3`
  - JSON output mode for Codex
  - human-readable text output mode for users
- SQLite state store
  - configurable path
  - default skill-local runtime state under the installed skill folder
  - separate SQLite database per target project
  - task records and concise task event history
  - migration history
- Optional install/export path later
  - copy or install skill into Codex skill directory
  - keep source project and installed skill distinguishable

## Design Principles

- The target project's own docs remain the source of truth.
- The database stores helper state, indexes, hashes, timestamps, and sanitized
  summaries, not raw private project content by default.
- Read-only inspection is the default.
- Target-project mutation requires explicit user intent.
- Verification should be runnable offline by default.
- Skill instructions should be concise; scripts should handle repeated and
  fragile tasks.
- Project-specific rules should be profiles, not hard-coded assumptions.
- Large Markdown task-status files are reference examples, not the preferred
  working state. The tool should store structured task state in SQLite and
  retrieve only the task slices needed for the current turn.

## Task Ordering Model

The tool should support both sequence-sensitive and free-order work so one
blocked task does not halt all productive task consumption.

- Sequential tasks capture work that must proceed in a declared order or within
  a dependency chain. They may be grouped into an ordered lane or milestone.
- Optional tasks capture work that can be selected in any order when its own
  dependencies, verification expectations, and review gate are clear.
- Task selection should prefer explicit dependencies and readiness over a
  single global implementation order. A blocked sequential lane must not hide
  ready optional tasks or ready tasks in other lanes.
- SQLite queries should be able to return a compact "next actionable tasks"
  view filtered by project, status, readiness, task kind, lane, priority, and
  dependency state.
- Reference files such as `references/KuraKoma_TASK_STATUS.md` may inform the
  quality bar and granularity, but their implied implementation order is not
  authority for this project.

## TASK_STATUS.md Replacement MVP Requirements

The first implementation should focus on replacing the practical functions that
large `TASK_STATUS.md` files served: explicitly register tasks, inspect current
and next work, mark blockers, and keep concise local task history. It should
not start as a general project-management system.

### MVP Scope

- Store task state in SQLite and retrieve compact slices instead of reading a
  large Markdown status file into context.
- Treat `task add` as an explicit user-approved registration step. Do not add
  separate draft, import, or approval workflows in the first version.
- Use `lane` plus `order` for sequence-sensitive tasks. Do not require a full
  task dependency graph in the first version.
- Keep verification and review as task fields in the first version, not as full
  workflow engines.
- Support JSON output for Codex and short text output for humans.
- Keep all commands local-first and offline-capable by default.
- Do not modify target projects; the MVP only writes to the
  task-governance-tool SQLite database.
- By default, create project-specific SQLite databases under a runtime state
  directory inside the installed `task-governance-tool` skill folder. This keeps
  the skill self-contained after installation while still keeping each target
  project's task state separate.
- Treat the skill-local runtime state directory as generated local data. It must
  not be part of committed source, exported skill packages, or static skill
  instructions.

### Deferred From MVP

- `task import` from Markdown or planning documents.
- A standalone `task depend` command or general dependency graph.
- `profile register` or persistent project profile authoring.
- `task approve` or draft-to-approved state transitions.
- Full `verify record`, review request generation, Git integration, dashboards,
  or automatic target-project mutation.

### Common CLI Options

All MVP commands should support:

```powershell
taskgov <command> --repo . --json
taskgov <command> --db C:\path\to\taskgov.sqlite
```

- `--repo`: target project root. Defaults to the current directory.
- `--db`: SQLite database path. Overrides the default skill-local project DB.
- `--json`: emit machine-readable output for Codex. Without it, emit concise
  human-readable text.
- `--read-only`: prohibit database creation, migration, or writes. Inspection
  commands behave as read-only by default even when this flag is omitted.

Default database path:

```text
<installed-skill-root>/state/projects/<project-id>/taskgov.sqlite
```

`<project-id>` should be deterministic from the canonical target project path:
use a sanitized project directory basename plus a short stable SHA-256 hash of
the canonical project path, such as `kurakoma-a1b2c3d4e5f6`. The computed path
should avoid leaking unnecessary path details while remaining stable across
turns.

### MVP Task Fields

The initial `tasks` model should include:

- `task_id`: stable ID such as `tg_task_...`.
- `project_id`: stable project ID.
- `title`: required short task title.
- `description`: optional task detail.
- `kind`: `sequential` or `optional`.
- `lane`: optional grouping string; required or auto-created for ordered
  sequential work.
- `lane_order`: integer order within a lane.
- `priority`: `low`, `normal`, `high`, or `urgent`.
- `status`: `ready`, `in_progress`, `blocked`, `review_pending`, `done`, or
  `cancelled`.
- `blocked_reason`: required when status is `blocked`.
- `review_tier`: integer `0`, `1`, or `2`.
- `verification`: short verification expectation or command label.
- `tags`: simple comma-separated labels.
- `created_at`, `updated_at`, and optional `completed_at` timestamps.

### MVP Commands

#### `taskgov db init`

Purpose: explicitly create or migrate the current project database.

Example:

```powershell
taskgov db init --repo . --json
```

Output should include database path, project ID, whether the database was
created, migration versions applied, and final schema version.

#### `taskgov db status`

Purpose: show whether the database and current project task state are usable.

Example:

```powershell
taskgov db status --repo . --json
```

This command is read-only by default. If the database is missing or needs
migration, it should report `needs_init` or `needs_migration` without creating
or changing the database. Output should include database path, schema version,
project ID, active task counts, blocked count, review-pending count, done count,
and next-actionable task count.

Active task counts include `ready`, `in_progress`, `blocked`, and
`review_pending`. They exclude `done` and `cancelled`.

#### `taskgov task add`

Purpose: register one explicit task as current task-governance-tool state.

Examples:

```powershell
taskgov task add --title "SQLite schema baseline" --kind sequential --lane TG-M1 --order 10 --priority high
taskgov task add --title "Review CLI help text" --kind optional --priority normal
```

Arguments:

- `--title`: required.
- `--description`: optional.
- `--kind`: `sequential` or `optional`; default `optional`.
- `--lane`: optional grouping string. Sequential tasks should normally provide
  one.
- `--order`: integer order within the lane. If omitted for sequential work, the
  CLI may append after the last task in the lane.
- `--priority`: `low`, `normal`, `high`, or `urgent`; default `normal`.
- `--status`: initial status; default `ready`.
- `--blocked-reason`: required when initial `--status` is `blocked`.
- `--review-tier`: `0`, `1`, or `2`; default may be `1` until project rules
  say otherwise.
- `--verification`: short verification expectation or command label.
- `--tags`: comma-separated tags.

If a sequential task omits `--lane` or `--order`, the CLI may store a
deterministic default lane and append order. Output must include the stored
`lane` and `lane_order` so any auto-filled ordering is visible.

If `task add` sets initial `--status blocked`, it must require
`--blocked-reason` and reject the command before storing any task when the
reason is missing.

#### `taskgov task list`

Purpose: return a compact filtered list instead of a full status document.

Examples:

```powershell
taskgov task list --status ready
taskgov task list --kind optional --status ready --limit 10
taskgov task list --lane TG-M1
```

Arguments:

- `--status`: filter by task status.
- `--kind`: filter by `sequential` or `optional`.
- `--lane`: filter by lane.
- `--priority`: filter by priority.
- `--tag`: filter by one tag.
- `--limit`: maximum rows; default around 20.
- `--include-done`: include completed tasks.

#### `taskgov task next`

Purpose: return the next ready tasks Codex can work on without reading the
whole task history.

Examples:

```powershell
taskgov task next --limit 5
taskgov task next --kind optional
taskgov task next --lane TG-M1
```

Initial selection rules:

- Include only tasks with `status=ready`.
- Include ready `optional` tasks directly.
- Include a ready `sequential` task only when earlier tasks in the same `lane`
  are `done` or `cancelled`.
- Exclude `in_progress`, `blocked`, `review_pending`, `done`, and `cancelled`
  tasks.
- Supported filters are `--kind`, `--lane`, `--priority`, and `--limit`.
- Default limit is `5`.
- Priority order is `urgent`, `high`, `normal`, `low`.
- Sort by priority rank, lane, lane order with nulls last, creation time, and
  task ID.
- If a sequential lane is blocked, still return ready optional tasks and ready
  tasks in other lanes.

#### `taskgov task show`

Purpose: show one task and its immediate context.

Example:

```powershell
taskgov task show tg_task_abc123 --json
```

Output should include title, description, kind, lane, lane order, priority,
status, blocked reason, review tier, verification expectation, tags, recent
notes or events, timestamps, and a suggested next action.

#### `taskgov task edit`

Purpose: update task state or metadata with one command instead of separate
specialized commands.

Examples:

```powershell
taskgov task edit tg_task_abc123 --status blocked --blocked-reason "User decision needed"
taskgov task edit tg_task_abc123 --status done
taskgov task edit tg_task_abc123 --priority high --review-tier 2
```

Arguments:

- `--title`, `--description`, `--kind`, `--lane`, `--order`, `--priority`.
- `--status`: one of the MVP statuses.
- `--blocked-reason`: required when setting `--status blocked`.
- `--review-tier`, `--verification`, `--tags`.
- `--add-note`: append a short local note or event.

`task block`, `task done`, and similar commands may be added later as aliases,
but they are not required for the first version.

## Candidate User Flows

The MVP user flows should stay close to the old `TASK_STATUS.md` role.

1. Check task database status
   - Input: repo path.
   - Output: database path, schema status, project ID, and compact task counts.

2. Register an explicit task
   - Input: title, optional description, task kind, lane/order, priority,
     review tier, verification expectation, and tags.
   - Output: stable task ID and the stored task summary.

3. Inspect current task state
   - Input: status, kind, lane, priority, or tag filters.
   - Output: a compact task list or one task's detail without loading all task
     history into context.

4. Select next actionable work
   - Input: repo path, optional kind, lane, priority, status, and limit filters.
   - Output: ready tasks only, with blocked lanes excluded while unrelated
     optional work remains visible.

5. Update task state
   - Input: task ID plus edited fields, status, blocked reason, or local note.
   - Output: updated task summary and event/history row.

6. Continue around blockers
   - Input: a task marked blocked, then a `task next` query.
   - Output: ready optional tasks or ready tasks in other lanes so work does not
     stop on one blocked chain.

Future flows may include project governance detection, richer execution-unit
start/close records, verification recording, review request generation, and
profile comparison after the MVP proves useful.

## Candidate CLI Commands

Names are placeholders. The first implementation should prioritize the
`TASK_STATUS.md` replacement MVP commands above.

```powershell
taskgov db init --repo . --json
taskgov db status --repo . --json
taskgov task add --repo . --title "Add migration gate" --kind sequential --lane TG-M1 --order 10 --review-tier 2 --json
taskgov task list --repo . --kind optional --status ready --json
taskgov task next --repo . --limit 5 --json
taskgov task show tg_task_... --json
taskgov task edit tg_task_... --status blocked --blocked-reason "User decision needed" --json
```

Later candidate commands may include:

```powershell
taskgov detect --repo C:\WorkSpace\KuraKoma --json
taskgov profile show --repo C:\WorkSpace\KuraKoma
taskgov verify record --task-id tg_task_... --command "python -m unittest"
taskgov review-template --task-id tg_task_... --json
taskgov db migrate --dry-run
```

## Candidate SQLite Tables

Early sketch only. The MVP may start with a smaller schema:

- `schema_migrations`
- `project_meta`
- `tasks`
- `task_events`
- `tool_events`

Later candidate tables may include:

- `project_profiles`
- `project_governing_docs`
- `profile_verification_commands`
- `task_lanes`
- `task_dependencies`
- `execution_units`
- `verification_runs`
- `review_requests`
- `review_findings`

MVP stable IDs:

- `project_id`
- `task_id`
- `task_event_id`

Later stable IDs may include:

- `profile_id`
- `execution_unit_id`
- `verification_run_id`
- `review_request_id`
- `review_finding_id`

## Project Profile Sketch

A project profile should capture:

- repository root
- governance docs and read order
- docs that are reference-only
- default task-start checklist
- review tier definitions
- verification commands by scope
- forbidden or approval-required operations
- privacy/logging constraints
- git behavior expectations
- generated/local artifact ignore patterns

Profile data may be stored in SQLite and exported to JSON/YAML later. The
profile must point to source docs rather than duplicating them as authority.

## Skill Package Sketch

Potential MVP skill layout after requirements are approved:

```text
task-governance-tool/
  SKILL.md
  agents/
    openai.yaml
  scripts/
    taskgov.py
    task_governance_tool/
      __init__.py
      cli.py
      storage.py
      tasks.py
      selection.py
  references/
    task_workflow.md
    cli_contracts.md
  state/              # generated local runtime state; not exported/committed
    projects/
      <project-id>/
        taskgov.sqlite
```

The source repository should also contain normal development files outside the
installable skill package:

```text
tests/
docs/
fixtures/
  task-status-mvp/
```

Runtime modules should remain inside the installable skill folder so the
installed skill is self-contained. Tests may import those modules by adding
`task-governance-tool/scripts/` to `sys.path`.

Later versions may add profile, verification, and review-template modules after
the MVP task-status replacement is stable.

## Initial Milestone Candidates

The detailed implementation roadmap is now maintained in
`docs/implementation-roadmap.md`. The milestone candidates below are retained
as historical planning context and high-level summary only.

### TG-M0 Requirements Baseline

Goal: settle the product boundary, privacy model, and
`TASK_STATUS.md` replacement MVP state model.

Included:

- AGENTS and plan baseline
- reference handling rules
- MVP task fields, statuses, command list, and selection rules
- first pass at simplified SQLite schema sketch
- open issues list

Verification:

- docs are internally consistent
- no reference file is treated as current project authority

Review:

- Tier 2 once the MVP requirements are treated as implementation-facing CLI and
  storage contracts

### TG-M1 Skill, CLI, And DB Skeleton

Goal: create the minimal installable skill shape, CLI scaffold, and initialized
local database boundary.

Included:

- `SKILL.md` with trigger metadata
- CLI help commands
- JSON/text output envelope
- configurable DB path
- skill-local per-project default DB path
- schema migrations and repository boundary
- `db init`
- `db status`
- tests for help, explicit DB initialization, and read-only `db status`

Excluded:

- task registration or mutation beyond initialization
- target-project mutation
- automatic installation into Codex skills directory

Verification:

- skill validation if helper scripts are available
- tests for JSON/text output envelopes
- CLI unit tests for help and `db status`
- temp-database initialization test through `db init`
- test that `db status` does not initialize or migrate a missing database
- test that default DB resolution creates separate DB paths for separate
  project roots
- no network dependency

Review:

- Tier 2 because skill trigger behavior and DB initialization are
  implementation-facing

### TG-M2 Task Registry MVP

Goal: implement the old `TASK_STATUS.md` task registration and inspection
functions in SQLite.

Included:

- `task add`
- `task list`
- `task show`
- `task edit`
- `tasks` and `task_events` repository behavior
- temp-database tests for status updates, blocked reasons, notes, and filtering

Excluded:

- target-project mutation
- import, approval, dependency graph, profiles, verification recording, review
  request generation, and Git integration

Verification:

- CLI contract tests for add/list/show/edit
- repository tests
- privacy tests for rejected or redacted fields
- no network dependency

Review:

- Tier 2

### TG-M3 Next Task Selection

Goal: implement the compact "what can I work on now?" query that avoids reading
large status files and works around blocked sequential lanes.

Included:

- `task next`
- sequential `lane` plus `lane_order` readiness rules
- optional task readiness
- priority/lane/order sorting
- blocked-lane behavior
- skill guidance for using `task next` at execution-unit boundaries

Excluded:

- standalone `task depend`
- general dependency graph
- automatic task selection without user/Codex judgment
- target-project mutation

Verification:

- CLI contract tests for `task next`
- fixture tests with blocked lanes and ready optional tasks
- end-to-end temp-db flow for add/edit/next/show
- no network dependency

Review:

- Tier 2

### TG-M4 Hardening And Forward Test

Goal: prove the skill and MVP CLI are useful on realistic tasks without leaking
private data or mutating targets unexpectedly.

Included:

- forward-test prompts
- privacy/logging audit
- docs closeout
- install/export decision
- representative dry-run on a small KuraKoma-style task set, with copied
  reference files treated as non-authoritative examples only
- small synthetic fixture under `fixtures/task-status-mvp/`

Excluded:

- full project profile system
- dashboards
- automatic review-agent spawning
- Git commits or PRs

Verification:

- full MVP test suite
- manual review of `task next` and `task show` outputs
- no target-project mutation

Review:

- Tier 2

## Decisions And Open Issues

Confirmed decisions:

- Formal MVP documents exist:
  - `docs/specification.md`
  - `docs/design.md`
  - `docs/implementation-roadmap.md`
- Skill name is `task-governance-tool`; CLI command is `taskgov`.
- The source workspace is Git-managed on the `main` branch. Generated state,
  local caches, SQLite databases, and root copied reference material are ignored
  by default.
- The source repository should contain the installable skill folder directly.
  Runtime modules live inside that skill folder; docs, tests, and fixtures live
  outside it as development and review surfaces.
- Default SQLite storage is skill-local and per-project:
  `<installed-skill-root>/state/projects/<project-id>/taskgov.sqlite`.
- `taskgov db init` is the explicit create/migrate command; `taskgov db status`
  is read-only by default and reports missing or migration-needed state without
  changing the database.
- Project IDs should use a sanitized project directory basename plus a short
  stable hash of the canonical project path.
- Tags stay as a simple comma-separated field in the MVP.
- `task block` and `task done` aliases are postponed; use `task edit` in the
  MVP.
- Raw command output retention is not supported in the MVP. Reconsider only
  after verification recording exists.
- Use a small synthetic KuraKoma-style fixture for MVP dry-runs without treating
  copied KuraKoma reference material as current project authority.
- If Codex skill helper scripts are unavailable, validate the skill with the
  documented self-check in `docs/implementation-roadmap.md`.

Open issues:

- Decide after the MVP whether to add profile detection, verification recording,
  review-template generation, dependency graphs, or Git integration.

## Implementation Execution Status

The approved implementation loop is consuming
`docs/implementation-roadmap.md`.

Current execution unit:

- `TG-M1.1 Repository And Test Harness`
  - status: completed
  - intended outcome: create the skill-local CLI entry point and unittest
    harness without database creation
  - write scope: `task-governance-tool/scripts/taskgov.py`,
    `task-governance-tool/scripts/task_governance_tool/__init__.py`,
    `task-governance-tool/scripts/task_governance_tool/cli.py`, `tests/`, and
    this status section
  - verification gate: CLI help exits successfully; unit test harness runs
    without network access
  - verification run: `python task-governance-tool\scripts\taskgov.py --help`;
    `python -m unittest discover -s tests`
  - review tier: Tier 1
  - review result: sub-agent review PASS, no blocking findings

- `TG-M1.2 JSON/Text Output And Error Envelope`
  - status: completed
  - intended outcome: implement the stable output envelope, text output
    baseline, and exit code mapping for later command handlers
  - write scope:
    `task-governance-tool/scripts/task_governance_tool/cli.py`, focused CLI
    tests, and this status section
  - verification gate: JSON envelope tests for success and validation error;
    help text tests for common options; argument parsing test proves
    `--read-only` reaches command handlers
  - verification run: `python -m unittest discover -s tests` (11 tests)
  - review tier: Tier 2
  - review result: two sub-agent reviews PASS, no blocking findings

- `TG-M1.3 Project ID And DB Path Resolution`
  - status: completed
  - intended outcome: resolve canonical repo identity, deterministic project
    ID, skill-local default DB path, and explicit `--db` override without
    creating files
  - write scope:
    `task-governance-tool/scripts/task_governance_tool/storage.py`,
    path-resolution tests, and this status section
  - verification gate: separate repo roots produce separate default DB paths;
    project IDs do not include private parent path details; explicit `--db`
    path resolution does not create files during read-only inspection
  - verification run: `python -m unittest discover -s tests` (17 tests);
    confirmed `task-governance-tool/state` does not exist
  - review tier: Tier 2
  - review result: two sub-agent reviews PASS, no blocking findings

- `TG-M1.4 Schema Migration And db init`
  - status: completed
  - intended outcome: initialize a migration-managed SQLite database through
    explicit `db init`, enforce the baseline schema constraints, and reject
    `--read-only` or project-mismatched writes safely
  - write scope:
    `task-governance-tool/scripts/task_governance_tool/storage.py`,
    `task-governance-tool/scripts/task_governance_tool/cli.py`,
    migration/repository tests, and this status section
  - verification gate: temp DB initialization; idempotent migration; schema
    constraint tests; project mismatch using explicit `--db`; `db init
    --read-only` does not create or modify files
  - verification run: `python -m unittest discover -s tests` (24 tests);
    `git diff --check`; manual temp-DB smoke check confirmed first init
    creates schema version 1, second init applies no migrations, `db init
    --read-only` creates no file or parent directory, invalid DB paths return
    structured JSON errors without traceback, and source
    `task-governance-tool/state` remains absent
  - review tier: Tier 2
  - review result: initial sub-agent review found a medium issue for raw
    traceback leakage on invalid DB paths; fixed in the same execution unit;
    two follow-up sub-agent reviews PASS, no blocking findings

- `TG-M1.5 Read-Only db status`
  - status: completed
  - intended outcome: inspect database existence, schema usability, project
    metadata, task counts, and provisional next-actionable count without
    creating, migrating, or writing files
  - write scope:
    `task-governance-tool/scripts/task_governance_tool/storage.py`,
    `task-governance-tool/scripts/task_governance_tool/cli.py`, `db status`
    tests, and this status section
  - verification gate: missing DB status does not create a database or parent
    state directory; initialized DB status returns the required JSON payload;
    migration-required and project-mismatch states are reported without
    mutation; text output remains concise
  - verification run: `python -m unittest discover -s tests` (33 tests);
    `git diff --check`; manual smoke check confirmed missing default DB
    reports `db_not_initialized` without creating `task-governance-tool/state`,
    initialized DB status returns schema version 1, text output is six concise
    lines, and WAL-mode status inspection creates no `-wal` or `-shm` sidecar
    files; regression coverage confirms an existing active WAL sidecar returns
    a structured non-success result instead of stale success counts
  - review tier: Tier 2
  - review result: initial sub-agent review found a medium issue for WAL
    sidecar creation; fixed in the same execution unit; follow-up review found
    a medium issue for stale success counts when existing WAL sidecars were
    present; fixed in the same execution unit; two final sub-agent reviews
    PASS, no blocking findings

- `TG-M2.1 Task Domain Validation`
  - status: completed
  - intended outcome: provide a shared task-domain validation layer for future
    task write paths, covering required fields, enum values, size limits, and
    deterministic privacy rejection before storage
  - write scope:
    `task-governance-tool/scripts/task_governance_tool/tasks.py`, validation
    tests, and this status section
  - verification gate: validation tests for each enum, required title, review
    tier integer/range, size limits, blocked reason requirement, privacy
    rejection patterns, and no raw command-output acceptance
  - verification run: `python -m unittest discover -s tests` (48 tests);
    `git diff --check`; regression coverage now includes prefixed secret keys,
    same-line stdout/stderr dumps, single raw diffs, and event-summary privacy
    rejection; header-shaped cookies, tokens, API keys, and obvious environment
    dump headings are also rejected; broader raw output/environment dump
    markers, indented raw diffs, and common JavaScript/Java stack traces are
    rejected; cookie assignments, password/client-secret/secret-key
    assignments, and delimiter-free stdout/environment dump headings are
    rejected; hyphenated secret-key assignments, command output/log headings,
    bare environment headings, standard output/error headings, async JavaScript
    and Java module stack frames, and spaced credential labels such as
    `api key` or `client secret key` are rejected; private-key assignments,
    environment dump/ENV VARS headings, stack-trace/log-output headings, Go
    panic/goroutine dumps, case-insensitive PEM markers, authorization
    assignments, delimiter-free stack/log headings, stdout/stderr dump
    headings, JSON-style credential/header keys, embedded one-line dump
    markers, generic authorization headers/assignments, raw stdout/stderr
    one-line markers, inline env assignments, and event-summary raw markers are
    rejected; JSON spaced credential keys and dash-delimited raw output/log
    markers are rejected; bearer/basic assignment forms, bare Basic auth
    values, bare multiline environment assignments, colon-delimited
    environment variable dumps, same-line stdout/stderr/standard output/error
    raw markers, hunk-only raw diffs, and .NET stack frames are rejected, while
    benign task wording containing these terms, including auth-related and raw
    heading-related prose titles, is accepted; trailing-punctuation
    Basic/Bearer auth values, all-alpha bearer tokens, mixed-case Windows-style
    environment dumps, and one-line raw output/log headings with ordinary
    payloads are rejected in stored free-form fields; title raw-output values
    such as `stdout: hello world` are rejected without blocking task-wording
    titles such as `Command output: improve formatting`; privacy rejection
    takes precedence over size-limit errors when both apply; short obvious
    Bearer values such as `Bearer secret`, `Bearer abc123`, and
    `Bearer sk-test` are rejected without blocking normal authentication
    wording such as `Bearer authentication support`; headed colon-delimited
    environment dumps and colon-trailing Basic/Bearer values are rejected
  - review tier: Tier 2
  - review result: multiple review rounds found privacy coverage gaps and
    false-positive risks for raw output, environment dumps, and auth-shaped
    values; all valid medium findings were fixed in the same execution unit;
    two final independent sub-agent reviews PASS, no blocking findings

- `TG-M2.2 task add`
  - status: completed
  - intended outcome: implement explicit task registration through
    `taskgov task add`, including validation reuse, default values, sequential
    lane/order auto-fill, task event creation, and structured JSON/text output
  - write scope:
    `task-governance-tool/scripts/task_governance_tool/tasks.py`,
    `task-governance-tool/scripts/task_governance_tool/cli.py`, task-add
    CLI/repository tests, and this status section
  - verification gate: add optional and sequential tasks through temp DBs;
    auto-filled lane/order; blocked initial status requires blocked reason;
    duplicate sequential lane/order is rejected; privacy rejection prevents
    storage; `task add --read-only` does not create or modify rows
  - verification run: `python -m unittest tests.test_task_add` (9 tests);
    `python -m unittest discover -s tests` (57 tests); `git diff --check`;
    coverage includes optional default registration, explicit sequential fields
    with blocked reason, sequential default lane/order append behavior,
    duplicate sequential lane/order rollback for task/event rows, privacy
    rejection without rows, read-only no-create/no-modify behavior, concise
    text output, and benign raw-output-related titles not being rejected by
    generated event summaries
  - review tier: Tier 2
  - review result: initial review found a medium issue where generated event
    summaries could re-reject benign accepted titles; fixed in the same
    execution unit; two final independent sub-agent reviews PASS, no blocking
    findings; remaining low risk is that duplicate-add failure can still update
    existing `project_meta.updated_at` during initialization, while task/event
    rows remain rolled back

- `TG-M2.3 task list`
  - status: completed
  - intended outcome: implement read-only compact task listing with supported
    filters and stable JSON/text output
  - write scope:
    `task-governance-tool/scripts/task_governance_tool/tasks.py`,
    `task-governance-tool/scripts/task_governance_tool/cli.py`, list/filter
    tests, and this status section
  - verification gate: filters for status, kind, lane, priority, tag, limit,
    and include-done; missing DB does not create files; task list is read-only
    and returns JSON data with `tasks`, `count`, and `limit`
  - verification run: `python -m unittest tests.test_task_list` (9 tests);
    `python -m unittest discover -s tests` (66 tests); `git diff --check`;
    coverage includes default active-only listing, status/kind/lane/priority/tag
    filters, limit and include-done behavior, structured validation errors,
    missing DB no-create behavior, migration-required and project-mismatch
    propagation without mutation, limit validation/clamping, and concise text
    output; implemented limit values are clamped to 100 rows, and
    `--include-done` includes terminal `done` and `cancelled` tasks
  - review tier: Tier 2
  - review result: two final independent sub-agent reviews PASS, no blocking
    findings; remaining low risk is that tag filtering is applied after
    fetching matching rows, which may do extra read work on very large local
    databases

- `TG-M2.4 task show`
  - status: completed
  - intended outcome: implement read-only single-task inspection with recent
    events, timestamps, and a suggested next action
  - write scope:
    `task-governance-tool/scripts/task_governance_tool/tasks.py`,
    `task-governance-tool/scripts/task_governance_tool/cli.py`, show/event
    tests, and this status section
  - verification gate: show existing task with events; unknown task returns
    structured `not_found`; missing DB does not create files; `task show` is
    read-only and returns JSON data with `task`, `events`, and
    `suggested_next_action`; text output remains concise
  - verification run: `python -m unittest tests.test_task_show` (8 tests);
    `python -m unittest discover -s tests` (74 tests); `git diff --check`;
    coverage includes existing task detail with recent events and timestamps,
    status-specific suggested actions, structured `not_found`, missing DB
    no-create behavior, migration-required and project-mismatch propagation
    without mutation, explicit `--read-only` success, concise text output,
    command-specific error payload shape for `not_found`, same-timestamp event
    ordering by insertion order, and no suggested action that names unimplemented
    commands
  - review tier: Tier 2
  - review result: initial sub-agent review found medium issues for missing
    command-specific `not_found` payload shape, same-timestamp event ordering,
    and suggested actions naming unimplemented commands; fixed in the same
    execution unit; two final independent sub-agent reviews PASS, no blocking
    findings

- `TG-M2.5 task edit`
  - status: completed
  - intended outcome: implement explicit task metadata/status updates with
    concise event history, completed timestamp transitions, blocked-reason
    enforcement, note support, and structured JSON/text output
  - write scope:
    `task-governance-tool/scripts/task_governance_tool/tasks.py`,
    `task-governance-tool/scripts/task_governance_tool/cli.py`, edit/event
    tests, and this status section
  - verification gate: status transition tests; blocked reason tests; add-note
    and event summary size-limit tests; privacy rejection prevents storage;
    `task edit --read-only` does not create or modify rows; JSON data contains
    `task`, `changed_fields`, and `event`
  - verification run: `python -m unittest tests.test_task_edit` (12 tests);
    `python -m unittest discover -s tests` (86 tests); `python
    task-governance-tool\scripts\taskgov.py task edit --help`;
    `git diff --check`; coverage includes metadata updates, status `done`
    completed timestamp set/clear behavior, blocked-reason enforcement,
    blocker clearing on unblock, note event creation without changed fields,
    long notes accepted up to the 2000-character note limit with concise
    1000-character-or-less event summaries, note size rejection without event
    rows, privacy rejection without storage, read-only no-create/no-modify
    behavior, missing DB no-create behavior, duplicate sequential order
    rollback, structured `not_found`, and concise text output
  - review tier: Tier 2
  - review result: initial sub-agent reviews found a medium issue where
    documented-valid 1001-2000 character notes were rejected by the shorter
    event-summary limit; fixed in the same execution unit by storing concise
    note event summaries while preserving the 2000-character note input limit;
    two final independent sub-agent reviews PASS, no blocking findings;
    remaining low risk is that failed edit paths after database initialization
    may still refresh existing `project_meta.updated_at`, while task/event rows
    remain rolled back

- `TG-M2.O1 Synthetic Task Fixture`
  - status: completed
  - intended outcome: add a small synthetic task-status fixture that can seed a
    temporary task-governance-tool database through public CLI commands
  - write scope: `fixtures/task-status-mvp/`, fixture tests, and this status
    section
  - verification gate: fixture contains optional tasks, sequential lanes,
    blocked tasks, review-pending tasks, and completed tasks; fixture can seed
    a temp DB through public CLI commands without target-project mutation
  - verification run: `python -m unittest tests.test_task_fixture` (1 test);
    `python -m unittest discover -s tests` (87 tests); `git diff --check`;
    `python -m json.tool fixtures/task-status-mvp/tasks.json`; coverage
    confirms the fixture seeds seven tasks through public `task add` CLI calls,
    includes ready/blocked/review-pending/done/cancelled statuses, includes
    optional and sequential tasks across `CORE` and `DOCS` lanes, and does not
    create the synthetic repo directory
  - review tier: Tier 1
  - review result: sub-agent review PASS, no blocking findings; remaining risk
    is that the fixture supports later `task next` validation but does not yet
    validate TG-M3 selection behavior

- `TG-M3.1 Selection Service`
  - status: completed
  - intended outcome: isolate next-actionable task selection from generic task
    list filtering
  - write scope: `task-governance-tool/scripts/task_governance_tool/selection.py`,
    selection service tests, and this status section
  - verification gate: ready optional tasks remain selectable when a sequential
    lane is blocked; later sequential tasks remain hidden until earlier lane
    tasks are `done` or `cancelled`; priority/lane/order sorting is
    deterministic
  - verification run: `python -m unittest tests.test_selection` (3 tests);
    `python -m unittest discover -s tests` (90 tests); `git diff --check`;
    coverage includes lane-local blocking, done/cancelled prior sequential
    tasks, default limit handling in the service, filters for kind/lane/priority,
    and deterministic priority/lane/order sorting
  - review tier: Tier 2
  - review result: two independent sub-agent reviews PASS, no blocking findings;
    remaining low risks are that tie-break sorting by `created_at`/`task_id` and
    earlier `in_progress`/`review_pending` sequential blockers could receive
    narrower regression tests later, while the implemented SQL covers them

- `TG-M3.2 task next`
  - status: completed
  - intended outcome: expose next-actionable task selection through the CLI and
    use the same selection service for `db status` next-actionable counts
  - write scope: `task-governance-tool/scripts/task_governance_tool/cli.py`,
    `task-governance-tool/scripts/task_governance_tool/selection.py`,
    `task-governance-tool/scripts/task_governance_tool/storage.py`, CLI tests,
    and this status section
  - verification gate: CLI filters and default limit; missing DB no-create
    behavior; end-to-end temp DB flow through init/add/edit/next/show; `db
    status` next-actionable count matches the selection service
  - verification run: `python -m unittest tests.test_task_next` (9 tests);
    `python -m unittest tests.test_cli_envelope tests.test_db_status
    tests.test_selection` (21 tests); `python
    task-governance-tool\scripts\taskgov.py task next --help`; `python -m
    unittest discover -s tests` (99 tests); `git diff --check`; coverage
    includes default limit and JSON contract, filters, blocked-lane behavior,
    `in_progress`/`review_pending` prior sequential blockers, missing DB
    no-create behavior, read-only no-event behavior, validation error payload
    shape, end-to-end temp DB init/add/edit/next/show flow, and `db status`
    total next-actionable count exceeding the default `task next` page limit
  - review tier: Tier 2
  - review result: initial independent reviews found one medium issue where
    `task.next` validation errors returned generic empty data instead of the
    command-specific `tasks`/`count`/`limit`/`selection_rules` shape; fixed in
    the same execution unit with a command-specific failure helper and
    regression tests; two final independent sub-agent reviews PASS, no blocking
    findings

- `TG-M4.1 SKILL.md And Skill References`
  - status: completed
  - intended outcome: add the installable skill metadata and concise references
    needed to use the bundled `taskgov` CLI without loading large project docs
  - write scope: `task-governance-tool/SKILL.md`,
    `task-governance-tool/agents/openai.yaml`,
    `task-governance-tool/references/task_workflow.md`,
    `task-governance-tool/references/cli_contracts.md`, and this status section
  - verification gate: skill metadata helper if available, otherwise roadmap
    fallback self-check; reference links resolve; `SKILL.md` frontmatter
    contains only `name` and `description`; no deferred MVP behavior is
    advertised
  - verification run: `generate_openai_yaml.py` and `quick_validate.py` were
    attempted but unavailable because `PyYAML` is not installed; fallback
    PowerShell self-check passed for required files, frontmatter
    name/description-only shape, folder/name match, trigger description,
    reference links, `agents/openai.yaml` metadata, ASCII content, and contents
    sections in long references; `python
    task-governance-tool\scripts\taskgov.py --help`; `python -m unittest
    discover -s tests` (99 tests); `git diff --check`
  - review tier: Tier 2
  - review result: initial independent reviews found one medium issue where
    `cli_contracts.md` overstated command-specific error data-shape guarantees;
    fixed in the same execution unit by narrowing the wording to implemented
    explicit empty data shapes, adding contents sections to long references, and
    aligning `SKILL.md` MVP wording with explicit local task-state updates; two
    final independent sub-agent reviews PASS, no blocking findings

- `TG-M4.2 Installed Skill Self-Containment Smoke Test`
  - status: completed
  - intended outcome: verify the installable skill folder can run its bundled
    CLI from an isolated copy and generated state/SQLite artifacts remain out of
    commits
  - write scope: self-containment smoke tests, `task-governance-tool/scripts/taskgov.py`
    if entrypoint path bootstrapping is needed, `.gitignore` only if generated
    SQLite sidecar ignore coverage is incomplete, and this status section
  - verification gate: isolated copied skill folder runs `scripts/taskgov.py
    --help`; runtime imports are satisfied by `task-governance-tool/scripts/`;
    generated `state/` and SQLite files are ignored by Git
  - verification run: `python -m unittest tests.test_skill_self_containment` (3
    tests); `python task-governance-tool\scripts\taskgov.py --help`; `python -m
    unittest discover -s tests` (102 tests); `git diff --check`;
    `git check-ignore -v` confirmed root `references/`, skill-local `state/`,
    SQLite DB files, WAL/SHM sidecars, and rollback journal sidecars are ignored
  - review tier: Tier 2
  - review result: initial independent reviews found medium issues where the
    smoke test could pass with site packages on `sys.path` and the static-copy
    test wrote temporary state into the real source skill tree; fixed in the
    same execution unit by running the isolated help smoke test with `python -I
    -S`, bootstrapping the entrypoint from its own `scripts/` folder, using only
    temporary skill-source copies for generated state exclusion tests, and
    expanding SQLite sidecar ignore coverage; two final independent sub-agent
    reviews PASS, no blocking findings; low-risk ignore-source and rollback
    journal coverage were also hardened

- `TG-M4.3 Full MVP Contract Test Pass`
  - status: completed
  - intended outcome: run the full MVP acceptance pass and representative
    text/JSON CLI checks against temporary databases only
  - write scope: tests, fixtures, docs updates only if behavior clarifications
    are required, and this status section
  - verification gate: full offline test suite; representative `db status`,
    `task list`, `task next`, and `task show` text/JSON outputs; temp DBs only;
    no target-project mutation
  - verification run: `python -m unittest discover -s tests` (102 tests);
    representative temp-DB smoke flow using only a temporary DB and synthetic
    target repo path: `db init`, `task add`, `db status`, `task list`, `task
    next`, and `task show` in JSON and text modes; smoke result confirmed
    `db status` next-actionable count `2`, `task list` count `5`, `task next`
    titles `Optional ready, First sequential`, `task show` title `First
    sequential`, and target repo path was not created; `git diff --check`
  - review tier: Tier 2
  - review result: two independent sub-agent reviews PASS, no blocking findings;
    reviewers confirmed the plan-only acceptance record matches TG-M4.3 roadmap
    gates and `docs/specification.md` acceptance criteria

- `TG-M4.O1 Release/Install Decision Note`
  - status: completed
  - intended outcome: record how the completed skill should be published and
    installed without mutating user skill directories
  - write scope: `docs/release-install.md` and this status section
  - verification gate: documentation consistency check with release packaging
    rules, install safety rules, and generated-state exclusions
  - verification run: documentation consistency self-check passed for required
    publication/install/exclusion terms and ASCII content; `git diff --check`
  - review tier: Tier 1
  - review result: sub-agent review PASS, no blocking findings; low-risk
    recommendation to distinguish source publication from installable release
    artifact was addressed before completion

- `TG-M5.O1 Conservative Regression Hardening`
  - status: completed
  - intended outcome: conservatively harden known low-risk gaps before real
    operation, without changing CLI behavior, SQLite schema, or skill trigger
    behavior, privacy behavior, or persistence semantics
  - write scope: `tests/test_selection.py`, `tests/test_task_next.py`, and this
    status section
  - verification gate: focused selection and `task next` tests; full offline
    unittest suite; `git diff --check`
  - review tier: Tier 1
  - verification run: `python -m unittest tests.test_selection
    tests.test_task_next` (13 tests); `python -m unittest discover -s tests`
    (103 tests); `git diff --check`; `python
    task-governance-tool\scripts\taskgov.py task next --help`; coverage now
    includes deterministic `created_at`/`task_id` tie-break sorting, null
    `lane_order` sorting, broader `task next` validation errors, and stronger
    read-only row-count checks for task/event tables
  - review result: sub-agent review PASS, no blocking findings; remaining
    low-risk notes are that read-only row-count checks do not detect in-place
    updates and the deterministic selection helper is schema-coupled by design

## Reference Material

- `references/KuraKoma_TASK_STATUS.md` is a copied reference example from
  `C:\WorkSpace\KuraKoma\TASK_STATUS.md`. It is intentionally not named
  `TASK_STATUS.md` at the root to avoid confusing it with this project's own
  task status.
