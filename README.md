# task-governance-tool

LLM quick read:

- This repository publishes a Codex skill in `task-governance-tool/`.
- The installable skill folder is `task-governance-tool/`, not the repository
  root.
- Use it to replace large `TASK_STATUS.md` files with local SQLite task state.
- It supports explicit task registration, task inspection, next-work selection,
  blocker handling, local task status updates, and completion commit evidence.
- It does not import planning files, manage dependency graphs, write Git state,
  create PRs/issues, run a service, or store raw logs/secrets.

`task-governance-tool` is a local-first Codex skill plus a stdlib Python CLI
named `taskgov`. It helps Codex and the user work from compact local task state
without treating that state as the source of truth. Target project `AGENTS.md`,
specs, design docs, tests, and current user decisions still govern the work.

## Install

Install from the skill folder path:

```text
https://github.com/<owner>/<repo>/tree/main/task-governance-tool
```

Or with the Codex skill installer helper:

```powershell
install-skill-from-github.py --repo <owner>/<repo> --path task-governance-tool
```

After installation, restart Codex so the skill metadata can be discovered.

## Minimal Workflow

Run commands from the installed skill folder:

```powershell
python scripts/taskgov.py db status --repo <target-project> --json
python scripts/taskgov.py db init --repo <target-project> --json
python scripts/taskgov.py task add --repo <target-project> --title "Example task" --json
python scripts/taskgov.py task next --repo <target-project> --json
python scripts/taskgov.py task show --repo <target-project> <task-id> --json
python scripts/taskgov.py task edit --repo <target-project> <task-id> --status done --verification-complete --review-complete --completion-commit-hash <hash> --json
```

Start with `db status`. It inspects without creating files. Use `db init` only
when local task tracking should be created or migrated for that target project.
Register only explicit user-approved tasks.

When no managed materials changed, complete with `--commit-not-required`
instead of `--completion-commit-hash <hash>`. `taskgov` records commit state but
does not create commits, branches, PRs, or issue comments.

By default, runtime state is stored under the installed skill folder:

```text
task-governance-tool/state/projects/<project-id>/taskgov.sqlite
```

Use `--db <path>` only when a project or user explicitly needs a different
database path.

## MVP Commands

- `taskgov db init`
- `taskgov db status`
- `taskgov task add`
- `taskgov task list`
- `taskgov task next`
- `taskgov task show`
- `taskgov task edit`

Inspection commands are read-only by default. Write commands record only to the
task-governance-tool SQLite database, not to target project files.

## Non-Goals

The MVP intentionally does not include:

- Markdown task import.
- `task approve`, `task depend`, or persistent project profiles.
- Verification-run recording beyond short task fields.
- Review request generation.
- Git commits, branches, PRs, issue comments, or target-project mutation.
- Network services, dashboards, sync, or cloud workflows.
- Raw command-output, stack-trace, prompt, diff, log, or secret retention.

## Development Checks

Run the local checks before publishing:

```powershell
python -m unittest discover -s tests
python task-governance-tool\scripts\taskgov.py --help
python task-governance-tool\scripts\taskgov.py task next --help
git diff --check
```

Generated runtime state, SQLite databases, root copied references, logs, caches,
and local scratch files must not be committed.

## Project Docs

- `docs/specification.md`: MVP product contract.
- `docs/design.md`: implementation design and boundaries.
- `docs/implementation-roadmap.md`: approved milestone and review gates.
- `docs/release-install.md`: release artifact and install decision.
