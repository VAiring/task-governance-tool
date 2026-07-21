# CLI Contracts

Use this reference when exact command arguments, JSON shapes, or error behavior
matter.

## Contents

- [Invocation](#invocation)
- [Commands](#commands)
- [`db init`](#db-init)
- [`db status`](#db-status)
- [`task add`](#task-add)
- [`task list`](#task-list)
- [`task next`](#task-next)
- [`task show`](#task-show)
- [`task edit`](#task-edit)
- [`web export`](#web-export)
- [Error Codes](#error-codes)

## Invocation

Run from the project-scoped installed skill folder, normally
`.agents/skills/task-governance-tool` inside the governed project:

```powershell
python scripts/taskgov.py <command> [options]
```

For a new target project, start with `db status`. It reports missing or
outdated databases without creating files. Use `db init` only when local task
tracking should be created or migrated for that project-scoped install. If the
skill is running from a user-wide or global install, confirm that non-standard
setup before writing state.

Common options:

- `--repo <path>`: target project root; defaults to the current directory.
- `--db <path>`: explicit SQLite database path override.
- `--json`: emit a stable JSON envelope.
- `--read-only`: reject write commands before creating, migrating, or writing.

All JSON output uses this envelope:

```json
{
  "ok": true,
  "command": "task.next",
  "project_id": "project-a1b2c3d4e5f6",
  "db_path": "C:\\path\\to\\taskgov.sqlite",
  "data": {},
  "warnings": [],
  "errors": []
}
```

Inspection commands are read-only by default: `db status`, `task list`,
`task next`, and `task show`.

Database write commands are `db init`, `task add`, and `task edit`. Only
`db init` may create or migrate a database. Other write commands require an
already initialized database at the current schema version; they return
`db_not_initialized` or `migration_required` without creating or migrating
files otherwise. `web export` never writes SQLite, but its normal mode writes
one generated HTML file after explicit user intent. Use `web export --read-only`
for a no-file-write preview.

## Commands

### `db init`

Create or migrate the local task database.

```powershell
python scripts/taskgov.py db init --repo <target-project> --json
```

`data`:

```json
{
  "created": true,
  "migrations_applied": [1, 2],
  "schema_version": 2
}
```

### `db status`

Inspect database readiness without creating or migrating files.

```powershell
python scripts/taskgov.py db status --repo <target-project> --json
```

`data`:

```json
{
  "exists": true,
  "needs_init": false,
  "needs_migration": false,
  "schema_version": 2,
  "counts": {
    "active": 3,
    "blocked": 1,
    "review_pending": 0,
    "done": 2,
    "next_actionable": 2
  }
}
```

### `task add`

Register one explicit task in a database previously prepared with `db init`.

```powershell
python scripts/taskgov.py task add --repo <target-project> --title "Update docs" --kind optional --priority normal --json
```

Useful options:

- `--description`
- `--kind sequential|optional`
- `--lane`
- `--order`
- `--priority low|normal|high|urgent`
- `--status ready|in_progress|blocked|review_pending|cancelled`
- `--blocked-reason`
- `--review-tier 0|1|2`
- `--verification`
- `--tags`

`data`: `task`, `event`.

An initial status of `done` is prohibited. Add the task in another supported
initial state, then complete it with `task edit --status done` so the normal
verification, review, and completion-evidence gates are enforced. Attempting
`task add --status done` returns `initial_done_forbidden` before any task or
event is stored.

### `task list`

Return compact filtered task rows.

```powershell
python scripts/taskgov.py task list --repo <target-project> --status ready --limit 20 --json
```

Filters:

- `--status`
- `--kind`
- `--lane`
- `--priority`
- `--tag`
- `--limit`
- `--include-done`

`data`: `tasks`, `count`, `limit`.

### `task next`

Return ready work candidates without loading all history.

```powershell
python scripts/taskgov.py task next --repo <target-project> --limit 5 --json
```

Filters:

- `--kind`
- `--lane`
- `--priority`
- `--limit`, default `5`

Selection rules:

- Include only `status=ready`.
- Include ready optional tasks directly.
- Include ready sequential tasks only when earlier same-lane tasks are `done` or
  `cancelled`.
- Exclude `in_progress`, `blocked`, `review_pending`, `done`, and `cancelled`.

`data`: `tasks`, `count`, `limit`, `selection_rules`.

### `task show`

Show one task plus recent event history and suggested next action.

```powershell
python scripts/taskgov.py task show --repo <target-project> <task-id> --json
```

`data`: `task`, `events`, `suggested_next_action`.

`task` includes completion commit trace fields:

- `completion_commit_required`
- `completion_commit_hash`

### `task edit`

Update task state or metadata.

```powershell
python scripts/taskgov.py task edit --repo <target-project> <task-id> --status blocked --blocked-reason "Waiting for ..." --json
```

Editable options:

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
- `--completion-commit-hash <hash>`: record the commit hash or unique revision
  ID that closes the task, and mark the task as requiring a completion commit.
- `--commit-not-required`: explicitly mark that no managed materials changed;
  this clears any stored completion commit hash.
- `--verification-complete`: record a concise command-time confirmation that
  required verification passed or has an approved exception.
- `--review-complete`: record a concise command-time confirmation that the
  required review gate passed or has a valid fallback.

`data`: `task`, `changed_fields`, `event`.

`--completion-commit-hash` and `--commit-not-required` are mutually exclusive.
`task edit --status done` requires both `--verification-complete` and
`--review-complete`. It also requires either a stored or newly supplied
`--completion-commit-hash`, unless `--commit-not-required` explicitly marks the
task as having no changed managed materials.

Complete with a commit hash or durable revision ID:

```powershell
python scripts/taskgov.py task edit --repo <target-project> <task-id> --status done --verification-complete --review-complete --completion-commit-hash <hash> --json
```

Complete when no managed materials changed:

```powershell
python scripts/taskgov.py task edit --repo <target-project> <task-id> --status done --verification-complete --review-complete --commit-not-required --json
```

`taskgov` records commit state only. It does not create commits or mutate the
target project. For Git projects, inspect changed materials from the stored
hash:

```powershell
git show --name-only <completion_commit_hash>
```

### `web export`

Render one self-contained, offline Task Viewer snapshot from the initialized
SQLite database:

```powershell
python scripts/taskgov.py web export --repo <target-project> --json
```

The default output is generated skill-local state:

```text
<installed-skill-root>/state/projects/<project-id>/viewer/task-viewer.html
```

Use `--read-only` to validate the database, snapshot, template, and resolved
output path without creating a directory or file. Use `--output <path>` only
after the user explicitly approves that complete destination. Explicit parents
must already exist and the filename must end in `.html` or `.htm`. An explicit
path inside the governed project is accepted only under the installed skill's
generated `state/` directory.

`data`:

```json
{
  "output_path": "C:\\path\\to\\task-viewer.html",
  "written": true,
  "replaced": false,
  "task_count": 3,
  "event_count": 7,
  "generated_at": "2026-07-17T00:00:00Z",
  "snapshot_version": 1
}
```

The generated file is stale until `web export` is explicitly run again. The
command does not start a server, open a browser, edit tasks, or write database
events. Databases using WAL mode are rejected before the snapshot connection so
even a preview does not create SQLite sidecar files.

After command and output resolution, every `web.export` error preserves this
fixed `data` shape. `output_path` is `null` only when output resolution itself
failed:

```json
{
  "output_path": null,
  "written": false,
  "replaced": false,
  "task_count": 0,
  "event_count": 0,
  "generated_at": null,
  "snapshot_version": 1
}
```

`output_path_invalid` and `output_parent_missing` use exit code 1.
`output_write_failed`, database readiness failures, WAL-state rejection, and
unexpected snapshot/template failures use exit code 2.

## Error Codes

Known error codes include:

- `invalid_argument`
- `invalid_status`
- `invalid_kind`
- `invalid_priority`
- `invalid_review_tier`
- `blocked_reason_required`
- `verification_required`
- `review_required`
- `commit_required`
- `completion_commit_conflict`
- `initial_done_forbidden`
- `privacy_rejected`
- `not_found`
- `db_not_initialized`
- `migration_required`
- `project_mismatch`
- `output_path_invalid`
- `output_parent_missing`
- `output_write_failed`
- `internal_error`

Some commands define an explicit empty `data` shape for specific error paths.
For example, `task.next` errors return empty `tasks`, `count`, `limit`, and
`selection_rules`. Other validation errors may use the generic empty object
shape unless the command contract or tests say otherwise.
