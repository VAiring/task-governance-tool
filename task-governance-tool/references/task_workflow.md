# Task Workflow

Use this reference when selecting, starting, blocking, or completing local
tasks with `task-governance-tool`.

## Contents

- [Source Of Truth](#source-of-truth)
- [Inspect Ready Work](#inspect-ready-work)
- [Selection Semantics](#selection-semantics)
- [Execution Unit Boundary](#execution-unit-boundary)
- [Blockers](#blockers)
- [Completion And Review](#completion-and-review)
- [Register Tasks](#register-tasks)

## Source Of Truth

The target project's governing docs and the current user request outrank the
SQLite task database. Before code or documentation work, read the target
project's applicable `AGENTS.md`, specs, design docs, tests, and local rules.

The task database stores compact execution state only. Do not treat it as a
hidden authority for product decisions.

## Inspect Ready Work

1. Check database state without mutation:

   ```powershell
   python scripts/taskgov.py db status --repo <target-project> --json
   ```

2. Ask for ready candidates:

   ```powershell
   python scripts/taskgov.py task next --repo <target-project> --limit 5 --json
   ```

3. Inspect the chosen task before acting:

   ```powershell
   python scripts/taskgov.py task show --repo <target-project> <task-id> --json
   ```

If the database is missing, initialize it only when the user intends to use this
local state store:

```powershell
python scripts/taskgov.py db init --repo <target-project> --json
```

## Selection Semantics

- `optional` tasks are actionable when `status=ready`.
- `sequential` tasks are actionable when `status=ready` and all earlier tasks in
  the same `lane` are `done` or `cancelled`.
- A blocked or incomplete sequential lane blocks only later work in that lane.
- Ready optional tasks and ready tasks in other lanes remain selectable.
- Sorting is by priority rank (`urgent`, `high`, `normal`, `low`), then lane,
  lane order with nulls last, creation time, and task ID.

## Execution Unit Boundary

Before starting a task, declare:

- intended outcome
- write scope
- verification gate
- review tier or review gate

Then mark the task in progress when the user-approved workflow expects local
tracking:

```powershell
python scripts/taskgov.py task edit --repo <target-project> <task-id> --status in_progress --json
```

## Blockers

When a task cannot continue, record the blocker concisely:

```powershell
python scripts/taskgov.py task edit --repo <target-project> <task-id> --status blocked --blocked-reason "Waiting for user decision on ..." --json
```

After recording a blocker, ask for another ready task with `task next`. Do not
let one blocked lane stop unrelated optional work or work in other lanes.

## Completion And Review

Move a task to `review_pending` when implementation is done but the required
review gate is not complete:

```powershell
python scripts/taskgov.py task edit --repo <target-project> <task-id> --status review_pending --json
```

Mark a task `done` only after the required verification and review gate pass:

```powershell
python scripts/taskgov.py task edit --repo <target-project> <task-id> --status done --json
```

Use `--add-note` for concise local notes. Keep notes sanitized and short; do not
store raw command output, stack traces, secrets, large diffs, or private chat
logs.

## Register Tasks

Register only explicit tasks. The MVP does not import large task files or create
draft dependency graphs.

```powershell
python scripts/taskgov.py task add --repo <target-project> --title "Implement task next" --kind sequential --lane TG-M3 --order 20 --priority high --review-tier 2 --json
```

For sequential tasks, omitted lane/order are auto-filled and returned in the
command output. For initially blocked tasks, `--blocked-reason` is required.
