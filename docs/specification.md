# task-governance-tool MVP Specification

Status: formal MVP specification baseline.

This document defines the first product contract for `task-governance-tool`.
It supersedes `plan.md` for MVP product behavior. `plan.md` remains the
roadmap, decision log, and open-issue holding area until a later implementation
task document replaces it.

## Product Goal

`task-governance-tool` helps Codex and the user run project work without loading
large task-status Markdown files into context. The MVP replaces the practical
functions of a large `TASK_STATUS.md`: register tasks, inspect current work,
select next actionable work, record blockers, and keep concise local task
history.

The tool must stay local-first, project-doc-respecting, and non-authoritative.
Target project governing documents remain the source of truth for project
decisions.

## MVP Scope

The MVP includes:

- A Codex skill named `task-governance-tool`.
- A Python stdlib-first CLI named `taskgov`.
- A SQLite state store using one database per target project.
- Task registration and inspection commands:
  - `taskgov db init`
  - `taskgov db status`
  - `taskgov task add`
  - `taskgov task list`
  - `taskgov task next`
  - `taskgov task show`
  - `taskgov task edit`
- JSON output for Codex and concise text output for humans.
- Skill-local generated runtime state under the installed skill folder.
- Offline operation by default.

## Non-Goals For MVP

The MVP does not include:

- Importing tasks from Markdown or planning documents.
- Draft/approval workflows such as `task approve`.
- A standalone dependency graph or `task depend`.
- Persistent project profile authoring or `profile register`.
- Verification-run recording beyond short task fields.
- Review request generation.
- Git commits, branches, PRs, issue comments, or other target-project mutation.
- Dashboards, services, network sync, or cloud workflows.
- Raw command-output retention.

## Skill Package Requirements

The installable skill folder name and `SKILL.md` frontmatter `name` must be
`task-governance-tool`.

The skill package should contain only files needed by Codex to use the skill:

```text
task-governance-tool/
  SKILL.md
  agents/
    openai.yaml
  scripts/
    taskgov.py
    task_governance_tool/
  references/
    task_workflow.md
    cli_contracts.md
```

The skill package may create generated local runtime state after installation:

```text
task-governance-tool/
  state/
    projects/
      <project-id>/
        taskgov.sqlite
```

`state/` is not part of the static skill package and must not be committed or
exported.

Runtime code required by `scripts/taskgov.py` must be included inside the skill
package so the installed skill is self-contained.

## Data Location

Default database path:

```text
<installed-skill-root>/state/projects/<project-id>/taskgov.sqlite
```

`<project-id>` must be deterministic from the canonical target project path. It
should use a sanitized target project directory basename plus a short stable
SHA-256 hash of the canonical path, for example:

```text
kurakoma-a1b2c3d4e5f6
```

The CLI must support `--db` to override the default path.

## Task Model

The MVP task record includes:

- `task_id`: stable ID such as `tg_task_...`.
- `project_id`: deterministic project ID.
- `title`: required short title.
- `description`: optional detail.
- `kind`: `sequential` or `optional`.
- `lane`: optional grouping string; normally required for ordered sequential
  work.
- `lane_order`: integer order within a lane.
- `priority`: `low`, `normal`, `high`, or `urgent`.
- `status`: `ready`, `in_progress`, `blocked`, `review_pending`, `done`, or
  `cancelled`.
- `blocked_reason`: required when status is `blocked`.
- `review_tier`: integer `0`, `1`, or `2`.
- `verification`: short verification expectation or command label.
- `tags`: comma-separated labels.
- `created_at`, `updated_at`, and optional `completed_at` timestamps.

The MVP may store task notes and state changes in a concise task event history.

## Task Ordering

Task selection must support both sequence-sensitive and free-order work.

- `optional` tasks are actionable when their status is `ready`.
- `sequential` tasks are actionable when their status is `ready` and all earlier
  tasks in the same `lane` are `done` or `cancelled`.
- A blocked sequential lane must not hide ready optional tasks or ready tasks in
  other lanes.

The MVP uses `lane` plus `lane_order` instead of a general dependency graph.

## CLI Requirements

All MVP commands must support:

- `--repo`: target project root; default current directory.
- `--db`: explicit SQLite path override.
- `--json`: machine-readable output for Codex.
- `--read-only`: prohibit database creation, migration, or writes. Inspection
  commands behave as read-only even when this flag is omitted.

Inspection commands must not create or migrate databases by default. Commands
that write to the task-governance-tool database must state what they recorded in
both JSON and text output.

### `taskgov db init`

Purpose: explicitly create or migrate the current project database.

Required output:

- database path
- project ID
- whether the database was created
- migration versions applied
- final schema version

### `taskgov db status`

Purpose: show whether the current task database is usable.

This command is read-only by default. If the database does not exist or needs a
schema migration, it must report that state without creating or migrating the
database. Use `taskgov db init` to create or migrate the database.

Required output:

- database path
- schema version
- whether the database exists
- whether initialization is needed
- whether migration is needed
- project ID
- active task count
- blocked task count
- review-pending task count
- done task count
- next-actionable task count

### `taskgov task add`

Purpose: register one explicit task as current state.

Required arguments:

- `--title`

Optional arguments:

- `--description`
- `--kind`, default `optional`
- `--lane`
- `--order`
- `--priority`, default `normal`
- `--status`, default `ready`
- `--review-tier`, default `1` unless project rules later say otherwise
- `--verification`
- `--tags`

Adding a task is an explicit registration action. The MVP does not create draft
tasks. If a sequential task omits `--lane` or `--order`, the CLI may store a
deterministic default lane and append order; command output must include the
stored `lane` and `lane_order` so the auto-filled ordering is visible.

### `taskgov task list`

Purpose: return compact filtered task lists.

Supported filters:

- `--status`
- `--kind`
- `--lane`
- `--priority`
- `--tag`
- `--limit`, default around 20
- `--include-done`

### `taskgov task next`

Purpose: return ready work candidates without loading all task history.

Selection rules:

- Include only `status=ready`.
- Include ready `optional` tasks directly.
- Include ready `sequential` tasks only when earlier tasks in the lane are
  complete or cancelled.
- Exclude `in_progress`, `blocked`, `review_pending`, `done`, and `cancelled`.
- Supported filters are `--kind`, `--lane`, `--priority`, and `--limit`.
- Default limit is `5`.
- Priority order is `urgent`, `high`, `normal`, `low`.
- Sort by priority rank, lane, lane order with nulls last, creation time, and
  `task_id` for deterministic ties.

### `taskgov task show`

Purpose: show one task and immediate context.

Required output:

- task fields
- recent notes or events
- timestamps
- suggested next action

### `taskgov task edit`

Purpose: update task state or metadata.

Editable fields:

- `--title`
- `--description`
- `--kind`
- `--lane`
- `--order`
- `--priority`
- `--status`
- `--blocked-reason`
- `--review-tier`
- `--verification`
- `--tags`
- `--add-note`

When setting `--status blocked`, `--blocked-reason` is required.

`task block` and `task done` aliases are postponed. Use `task edit` in the MVP.

## JSON Output

JSON output must be stable enough for Codex to consume. Each command should emit
a top-level object with:

- `ok`: boolean.
- `command`: command name.
- `project_id`: when applicable.
- `db_path`: when applicable.
- `data`: command-specific payload.
- `errors`: list of structured errors.
- `warnings`: list of structured warnings.

Human-readable output should be concise and should not replace JSON contracts.

Command names must be stable:

- `db.init`
- `db.status`
- `task.add`
- `task.list`
- `task.next`
- `task.show`
- `task.edit`

Exit codes:

- `0`: success.
- `1`: validation or user-correctable command error.
- `2`: database, migration, or unexpected tool error.

Timestamps in JSON must use UTC ISO-8601 strings. Paths should be emitted as
normalized absolute strings for local display; IDs must not encode private path
details.

Required `data` payloads:

- `db.init`: `created`, `migrations_applied`, `schema_version`.
- `db.status`: `exists`, `needs_init`, `needs_migration`, `schema_version`,
  `counts`.
- `task.add`: `task`, `event`.
- `task.list`: `tasks`, `count`, `limit`.
- `task.next`: `tasks`, `count`, `limit`, `selection_rules`.
- `task.show`: `task`, `events`, `suggested_next_action`.
- `task.edit`: `task`, `changed_fields`, `event`.

Task objects in JSON must use the same field names as the task model. The
`review_tier` value must be an integer, not a string.

Required error codes:

- `invalid_argument`
- `invalid_status`
- `invalid_kind`
- `invalid_priority`
- `invalid_review_tier`
- `blocked_reason_required`
- `privacy_rejected`
- `not_found`
- `db_not_initialized`
- `migration_required`
- `project_mismatch`
- `internal_error`

## Privacy And Safety

The MVP must:

- Write only to the task-governance-tool SQLite database by default.
- Never modify target project files.
- Never store API keys, tokens, cookies, authorization headers, raw provider
  bodies, full private prompts, full chat logs, large raw diffs, raw stdout, raw
  stderr, stack traces, or environment dumps.
- Treat `state/` as generated local runtime state.
- Keep root copied reference material non-authoritative.

Free-form fields are allowed only as concise sanitized task metadata. The CLI
must reject obvious secret, log, or diff content before storing user text.
Warnings may be used only for non-stored borderline content or non-blocking
normalization notes.

MVP size limits:

- `title`: 200 characters.
- `description`: 4000 characters.
- `verification`: 500 characters.
- `tags`: 500 characters.
- `--add-note`: 2000 characters.
- event `summary`: 1000 characters.

The CLI must reject obvious secrets and raw dump patterns, including bearer
tokens, authorization headers, private key blocks, `password=`, `token=`,
`api_key=`, raw stack traces, raw stdout/stderr dumps, and large raw diffs.

## Acceptance Criteria

The MVP is acceptable when:

- The skill package metadata validates or passes the documented self-check.
- `taskgov db status` inspects a project database without creating or migrating
  it.
- `taskgov db init` creates or migrates a project database safely.
- `taskgov task add/list/show/edit/next` work against a temporary database.
- `task next` correctly works around blocked sequential lanes.
- JSON outputs are tested for shape and key fields.
- Free-form privacy rejection and size-limit behavior are tested.
- No command mutates target project source files.
- Generated SQLite databases and root copied references are ignored by Git.
