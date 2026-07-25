# task-governance-tool

LLM quick read:

- This repository publishes a Codex skill in `task-governance-tool/`.
- The installable skill folder is `task-governance-tool/`, not the repository
  root.
- Install it per governed project under `.agents/skills/task-governance-tool`;
  user-wide installs are not recommended for normal use.
- Use it to replace large `TASK_STATUS.md` files with local SQLite task state.
- It supports explicit task registration, current/next task inspection,
  pause/resume and blocker handling, sequential guards, typed completion
  evidence, structured review receipts/findings, and an explicitly requested
  offline static Task Viewer.
- It does not import planning files, manage dependency graphs, write Git state,
  create PRs/issues, run a service, provide live browser editing, or store raw
  logs/secrets.

`task-governance-tool` is a local-first Codex skill plus a stdlib Python CLI
named `taskgov`. It helps Codex and the user work from compact local task state
without treating that state as the source of truth. Target project `AGENTS.md`,
specs, design docs, tests, and current user decisions still govern the work.

## Install

Install the skill folder into each governed project that needs task tracking:

```text
<target-project>\.agents\skills\task-governance-tool
```

Use the release artifact or this repository's `task-governance-tool/` folder as
the source package. Do not install the MVP into a user-wide skill directory such
as `%USERPROFILE%\.codex\skills` for normal governed-project use; that makes one
copy responsible for unrelated project task state.

Prefer the release artifact. If copying from a development working tree,
exclude `state/`, caches, generated viewers, and SQLite files instead of making
an unfiltered recursive copy.

Before running write commands in the target project, ensure generated state is
ignored there:

```text
.agents/skills/task-governance-tool/state/
*.sqlite
*.sqlite3
*.db
*.sqlite-wal
*.sqlite-shm
*.sqlite-journal
*.sqlite3-wal
*.sqlite3-shm
*.sqlite3-journal
*.db-wal
*.db-shm
*.db-journal
```

After project-scoped installation, verify the ignore rules, run `db status`,
and—with explicit intent to start local tracking—run `db init`. Skill package
creation itself must not initialize a target database. Restart Codex or start a
new session from inside that project so the skill metadata can be discovered.

## Minimal Workflow

Run commands from the project-scoped installed skill folder:

```powershell
python scripts/taskgov.py db status --repo <target-project> --json
python scripts/taskgov.py db init --repo <target-project> --json
python scripts/taskgov.py task add --repo <target-project> --title "Example task" --json
python scripts/taskgov.py task current --repo <target-project> --json
python scripts/taskgov.py task current --repo <target-project> --status paused --json
python scripts/taskgov.py task next --repo <target-project> --json
python scripts/taskgov.py task show --repo <target-project> <task-id> --json
python scripts/taskgov.py review target set --repo <target-project> <task-id> --kind git_commit --revision <hash> --json
python scripts/taskgov.py review receipt add --repo <target-project> <task-id> --reviewer <reviewer-a> --kind independent --verdict pass --summary "No blocking findings" --json
python scripts/taskgov.py task edit --repo <target-project> <task-id> --status done --verification-complete --review-complete --completion-commit-hash <hash> --json
```

Start with `db status`. It inspects without creating files. Use `db init` only
when local task tracking should be created or migrated for that target project.
Register only explicit user-approved tasks.

`db status` reports the exact paused count. A successful `task next` keeps
returning ready work but adds `paused_tasks_present` when paused tasks exist;
use the bounded `task current --status paused` view to rediscover them.

Tier 1 normally needs one independent PASS; a documented self-review fallback
is allowed when independent tooling is unavailable. Tier 2 normally needs two
distinct reviewers for the same target generation; its self-review fallback
also requires explicit user approval. A meaningful fix advances the target and
needs fresh review. These are the LLM review decisions; Git/ordering/evidence
checks are deterministic and add no LLM judgment. When no managed materials
changed, complete with `--commit-not-required`; approved external durable
revisions use the explicit `external_revision` options. `taskgov` validates
evidence but does not create commits, branches, PRs, or issue comments.

By default, runtime state is stored under the project-scoped installed skill
folder:

```text
<target-project>/.agents/skills/task-governance-tool/state/projects/<project-id>/taskgov.sqlite
```

Use `--db <path>` only when a project or user explicitly needs a different
database path.

## Offline Task Viewer

When the user explicitly asks to create or regenerate a browser-readable task
snapshot, run:

```powershell
python scripts/taskgov.py web export --repo <target-project> --json
```

Preview without creating a directory or file:

```powershell
python scripts/taskgov.py web export --repo <target-project> --read-only --json
```

The default output is:

```text
<target-project>/.agents/skills/task-governance-tool/state/projects/<project-id>/viewer/task-viewer.html
```

Use `--output <html-path>` only after the user approves that complete
destination; its parent must already exist. The HTML is self-contained and
opens through `file://` without a server or network. Snapshot v3 includes typed
completion and bounded structured review evidence. It is a timestamped
snapshot, not a live view: task changes appear only after an explicitly
requested regeneration. The page cannot edit tasks, and `taskgov` does not open
a browser automatically.

## Commands

- `taskgov db init`
- `taskgov db status`
- `taskgov task add`
- `taskgov task list`
- `taskgov task next`
- `taskgov task current`
- `taskgov task show`
- `taskgov task edit`
- `taskgov review target set`
- `taskgov review receipt add`
- `taskgov review finding add`
- `taskgov review finding resolve`
- `taskgov web export`

Inspection commands are read-only by default. Write commands record only to the
task-governance-tool SQLite database, except explicitly requested `web export`,
which writes one generated HTML snapshot and never writes SQLite.

## Non-Goals

The current release intentionally does not include:

- Markdown task import.
- `task approve`, `task depend`, or persistent project profiles.
- Verification receipts beyond short task fields.
- Stale-work warnings, persistent checkpoints, history pagination, and
  parent/child or acceptance-checklist structures.
- Review request generation or raw review transcript retention.
- Git commits, branches, PRs, issue comments, or target-project mutation.
- Network services, live dashboards, browser editing, sync, or cloud workflows.
- Raw command-output, stack-trace, prompt, diff, log, or secret retention.

## Development Checks

Run the local checks before publishing:

```powershell
python -m unittest discover -s tests
python task-governance-tool\scripts\taskgov.py --help
python task-governance-tool\scripts\taskgov.py task next --help
python task-governance-tool\scripts\taskgov.py web export --help
git diff --check
```

Generated runtime state, SQLite databases, root copied references, logs, caches,
and local scratch files must not be committed.

## Project Docs

- `docs/specification.md`: MVP product contract.
- `docs/design.md`: implementation design and boundaries.
- `docs/implementation-roadmap.md`: approved milestone and review gates.
- `docs/release-install.md`: release artifact and install decision.
