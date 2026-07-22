# Release And Install Decision

This note records how `task-governance-tool` should be published after MVP
completion. It is a release decision note only; it does not authorize installing
or overwriting a project or user skill directory.

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

The skill should be installed per governed project, not as a user-wide shared
skill. A separate project-scoped copy keeps task state attached to the project
whose tasks it manages.

## Release Artifact Contents

The installable artifact should contain:

```text
task-governance-tool/
  SKILL.md
  agents/
    openai.yaml
  assets/
    task-viewer.template.html
  scripts/
    taskgov.py
    task_governance_tool/
      __init__.py
      cli.py
      storage.py
      tasks.py
      ordering.py
      selection.py
      completion.py
      reviews.py
      viewer.py
  references/
    task_workflow.md
    cli_contracts.md
```

The artifact must exclude:

- `state/`
- `*.sqlite`, `*.sqlite3`, `*.db`
- SQLite sidecars such as `*-wal`, `*-shm`, and `*-journal`
- generated `task-viewer.html` snapshots
- root `references/`
- `tests/`, `fixtures/`, and development-only docs
- caches, logs, temporary files, and local editor files

## Install Path

For normal use, install the artifact into the governed target project:

```text
<target-project>\.agents\skills\task-governance-tool
```

This repo-scoped location is the Codex skill discovery path for a project. It
also keeps the default database path project-local:

```text
<target-project>\.agents\skills\task-governance-tool\state\projects\<project-id>\taskgov.sqlite
```

Before installing or updating, show the exact destination path and obtain
explicit user approval. Installing into a target project's `.agents/skills`
directory is a target-project file mutation. If the destination already exists,
do not overwrite it without a separate explicit update decision.

Project-scoped setup happens after installation, not while the artifact is
built. Before running `db init` or any task-state write command, make sure
generated state is kept out of commits. The target project should ignore at
least:

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

Then inspect and initialize explicitly:

```powershell
python scripts/taskgov.py db status --repo <target-project> --json
python scripts/taskgov.py db init --repo <target-project> --json
python scripts/taskgov.py db status --repo <target-project> --json
```

Only `db init` creates or migrates the database. The current release migrates
supported schema-v2 databases through schema v5 while retaining historical
task/event IDs and completion hashes; keep a normal project backup before any
release update even though migration is transactional and repeatable.

## Viewer Runtime State

After installation, an explicitly requested `web export` writes the default
viewer to:

```text
<installed-skill-root>\state\projects\<project-id>\viewer\task-viewer.html
```

Use an explicit `--output` only after the user approves that complete path; its
parent must already exist. Snapshot v3 includes typed completion and bounded
structured review evidence. The HTML is stale until the user requests
regeneration. It has no server, live database refresh, browser editing, or
automatic browser launch. Generated viewers remain runtime state and must not
be added to the release artifact.

User-wide installation locations such as
`%USERPROFILE%\.codex\skills\task-governance-tool`, `%CODEX_HOME%\skills`, or a
personal global skills directory are not recommended for normal use. Use
them only for explicit local experimentation, preferably with an explicit
`--db` path, because one global copy can otherwise accumulate task state for
multiple unrelated projects.

## Pre-Release Checks

Before creating a release artifact:

1. Confirm the worktree is clean.
2. Run the full offline test suite.
3. Run the fallback skill self-check or official skill validation helper when
   available.
4. Run the installed skill self-containment smoke test.
5. Confirm an isolated installed copy can run `web export` and that the
   artifact includes `assets/task-viewer.template.html` and `viewer.py`.
6. Confirm generated `state/`, SQLite files, generated `task-viewer.html`, root
   copied references, logs, and caches are ignored and absent from the artifact.
7. Confirm `task-governance-tool/SKILL.md` frontmatter contains only `name` and
   `description`, and the `name` matches the folder.

## Publication Notes

Initial publication should avoid network services and cloud dependencies. The
skill is local-first and should remain usable offline.

Versioning follows the runtime package version in
`task-governance-tool/scripts/task_governance_tool/__init__.py`. Release notes
should name current/next task inspection, pause/resume, sequential transition
guards, typed completion evidence with read-only Git validation, structured
review gates, schema-v5 migration, and snapshot-v3 offline Viewer export.
The TG-M8 release candidate is version `0.2.0` because it adds new commands,
schema migrations, and completion-gate behavior beyond the `0.1.0` trial.
