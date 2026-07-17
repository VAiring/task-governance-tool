---
name: task-governance-tool
description: Project-scoped local-first task tracking for Codex using the bundled taskgov CLI and skill-local SQLite state. Use when planning or registering explicit tasks, initializing or inspecting task state, selecting next actionable work, handling blockers, updating or completing tasks with verification, review, and completion commit evidence, or creating or regenerating a user-requested offline static Task Viewer.
---

# Task Governance Tool

Use this skill to work from compact local task state instead of loading large
task-status documents. Treat the target project's `AGENTS.md`, specs, design
docs, tests, and user decisions as the source of truth; the SQLite database is
only a local task-state helper.

This skill is intended to be installed per governed project, normally under the
target project's `.agents/skills/task-governance-tool` directory. Do not use a
user-wide install as the normal task manager for multiple projects.

## Quick Start

Run the bundled CLI from the project-scoped installed skill folder:

```powershell
python scripts/taskgov.py db status --repo <target-project> --json
python scripts/taskgov.py task next --repo <target-project> --json
python scripts/taskgov.py task show --repo <target-project> <task-id> --json
```

First-use flow:

1. Run `db status` to inspect without creating files.
2. If the database is missing and the user intends to use local task tracking,
   run `db init`.
3. If there are no tasks yet, register explicit user-approved tasks with
   `task add`; do not import large task files or invent dependency graphs.
4. Use `task next` and `task show` to choose and inspect work before acting.

Use `--db <path>` only when the user or project explicitly needs a database
outside the skill-local default. The default database lives under this installed
skill folder at `state/projects/<project-id>/taskgov.sqlite`.
If this skill was discovered from a user-wide or global install, do not
initialize task state until the user confirms that they want that non-standard
setup or installs a project-scoped copy.

## Static Task Viewer

Create or regenerate the offline viewer only when the current user explicitly
asks for that HTML artifact. A request only to inspect or summarize task state
authorizes `task list`, `task next`, or `task show`, not an HTML write.

Use the bundled entry point and command names exactly:
`python scripts/taskgov.py web export --repo <target-project>`. There is no
`viewer` command group, `--project-root` option, or guaranteed global `taskgov`
executable; do not invent aliases.

Preview the resolved path and counts without writing:

```powershell
python scripts/taskgov.py web export --repo <target-project> --read-only --json
```

After explicit user intent, generate the default snapshot:

```powershell
python scripts/taskgov.py web export --repo <target-project> --json
```

The default file is
`state/projects/<project-id>/viewer/task-viewer.html` under this installed
skill. Use `--output <html-path>` only when the user approves that complete
destination; its parent must already exist. The generated page is a stale
snapshot until the command is explicitly run again. It has no server, live
refresh, browser-side SQLite access, or task-edit controls, and the CLI does not
open a browser automatically.

## Operating Rules

- Inspect before writing: `db status`, `task list`, `task next`, and `task show`
  must not create or migrate databases.
- Use `db init` only when the user intends to initialize or migrate the local
  task database.
- Use `task add` and `task edit` only for explicit task-state registration or
  updates; they write only to the task-governance-tool database.
- Use writing `web export` mode only for an explicit create/regenerate request;
  it writes one generated HTML snapshot and never writes SQLite.
- Complete tasks only after required verification and review are done, then
  record either a completion commit hash or an explicit no-managed-materials
  decision with `task edit --status done`.
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

This version supports task registration, inspection, next-work selection,
blocker updates, completion commit evidence recorded on task rows, and an
explicitly requested offline static viewer. It does not create commits,
branches, PRs, issue comments, review requests, persistent project profiles,
dependency graphs, network services, live dashboards, browser edit controls,
or unapproved target-project mutation.
