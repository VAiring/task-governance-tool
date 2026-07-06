# Release And Install Decision

This note records how `task-governance-tool` should be published after MVP
completion. It is a release decision note only; it does not authorize installing
or overwriting a user skill directory.

## Decision

Publish the project from this Git repository, and treat the
`task-governance-tool/` directory as the installable skill package.

The first public publication should distinguish source review material from the
installable skill artifact:

- Source publication: a Git tag that contains the reviewed source repository.
- Installable release artifact: an archive containing only the installable
  `task-governance-tool/` skill folder.

Do not publish generated runtime state, copied root reference material, test
outputs, caches, logs, or local databases.

## Release Artifact Contents

The installable artifact should contain:

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
```

The artifact must exclude:

- `state/`
- `*.sqlite`, `*.sqlite3`, `*.db`
- SQLite sidecars such as `*-wal`, `*-shm`, and `*-journal`
- root `references/`
- `tests/`, `fixtures/`, and development-only docs
- caches, logs, temporary files, and local editor files

## Install Path

For a local Codex skill install, use a destination such as:

```text
%USERPROFILE%\.codex\skills\task-governance-tool
```

or, when `CODEX_HOME` is explicitly configured:

```text
%CODEX_HOME%\skills\task-governance-tool
```

Before installing or updating, show the exact destination path and obtain
explicit user approval. If the destination already exists, do not overwrite it
without a separate explicit update decision.

## Pre-Release Checks

Before creating a release artifact:

1. Confirm the worktree is clean.
2. Run the full offline test suite.
3. Run the fallback skill self-check or official skill validation helper when
   available.
4. Run the installed skill self-containment smoke test.
5. Confirm generated `state/`, SQLite files, root copied references, logs, and
   caches are ignored and absent from the artifact.
6. Confirm `task-governance-tool/SKILL.md` frontmatter contains only `name` and
   `description`, and the `name` matches the folder.

## Publication Notes

Initial publication should avoid network services and cloud dependencies. The
skill is local-first and should remain usable offline.

Versioning can start at the runtime package version in
`task-governance-tool/scripts/task_governance_tool/__init__.py`. If a Git tag is
created, use a clear prefix such as `v0.1.0` and include a short note that this
MVP supports local task registration, task inspection, next-task selection,
blocker handling, and explicit local task-state updates.
