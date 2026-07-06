---
name: task-governance-tool
description: Local-first task governance for Codex using the bundled taskgov CLI and SQLite state. Use when planning or consuming project tasks, inspecting task status, selecting next actionable work, handling blockers, or updating local task state during high-discipline task execution.
---

# Task Governance Tool

Use this skill to work from compact local task state instead of loading large
task-status documents. Treat the target project's `AGENTS.md`, specs, design
docs, tests, and user decisions as the source of truth; the SQLite database is
only a local task-state helper.

## Quick Start

Run the bundled CLI from this skill folder:

```powershell
python scripts/taskgov.py db status --repo <target-project> --json
python scripts/taskgov.py task next --repo <target-project> --json
python scripts/taskgov.py task show --repo <target-project> <task-id> --json
```

Use `--db <path>` only when the user or project explicitly needs a database
outside the skill-local default. The default database lives under this installed
skill folder at `state/projects/<project-id>/taskgov.sqlite`.

## Operating Rules

- Inspect before writing: `db status`, `task list`, `task next`, and `task show`
  must not create or migrate databases.
- Use `db init` only when the user intends to initialize or migrate the local
  task database.
- Use `task add` and `task edit` only for explicit task-state registration or
  updates; they write only to the task-governance-tool database.
- Do not modify target-project files, Git state, issues, PRs, or external
  services merely because this skill inspected task state.
- Do not store secrets, raw logs, stack traces, environment dumps, large raw
  diffs, full private prompts, or full chat logs in task fields.

## Workflow References

Read [references/task_workflow.md](references/task_workflow.md) when choosing
work, starting an execution unit, updating blockers, or deciding when a task is
complete.

Read [references/cli_contracts.md](references/cli_contracts.md) when you need
command arguments, JSON payload shapes, error behavior, or examples.

This MVP supports task registration, inspection, and explicit local task-state
updates only. Do not advertise or invent verification recording, review request
generation, persistent project profiles, dependency graphs, Git integration,
network services, or target-project mutation.
