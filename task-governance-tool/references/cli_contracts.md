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
- [Error Codes](#error-codes)

## Invocation

Run from the installed skill folder:

```powershell
python scripts/taskgov.py <command> [options]
```

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

Write commands are `db init`, `task add`, and `task edit`.

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
  "migrations_applied": [1],
  "schema_version": 1
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
  "schema_version": 1,
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

Register one explicit task.

```powershell
python scripts/taskgov.py task add --repo <target-project> --title "Update docs" --kind optional --priority normal --json
```

Useful options:

- `--description`
- `--kind sequential|optional`
- `--lane`
- `--order`
- `--priority low|normal|high|urgent`
- `--status ready|in_progress|blocked|review_pending|done|cancelled`
- `--blocked-reason`
- `--review-tier 0|1|2`
- `--verification`
- `--tags`

`data`: `task`, `event`.

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

`data`: `task`, `changed_fields`, `event`.

## Error Codes

Known error codes include:

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

Some commands define an explicit empty `data` shape for specific error paths.
For example, `task.next` errors return empty `tasks`, `count`, `limit`, and
`selection_rules`. Other validation errors may use the generic empty object
shape unless the command contract or tests say otherwise.
