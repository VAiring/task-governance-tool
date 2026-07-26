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
      git_snapshot.py
      handoffs.py
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

Only `db init` creates or migrates the database. Version `0.4.0` uses schema v7
and migrates supported schema-v2 through v6 databases through the explicit
ordered migration path while retaining task/event IDs, completion hashes, and
structured review history. Schema v7 adds an empty local handoff outbox without
rewriting existing tasks or evidence. Skill installation or an ordinary task
command does not migrate a database. Back up the project-local database before
updating the skill, then run `db init` explicitly. Migrations are transactional,
repeatable, and never downgrade a database; an older runtime rejects a newer
schema. An incomplete migration history is not repaired by inference:
`db init` returns `migration_required` without mutation so the operator can
restore a valid backup or inspect the history.

## Git Snapshot Completion Workflow

Version `0.3.0` supports review before the project creates its completion
commit. Stage only the intended project changes through the project's own Git
workflow, capture a snapshot target without a caller revision, collect the
required reviews, create the project commit, and then complete the task:

```powershell
git add <intended-project-paths>
python scripts/taskgov.py review target set --repo <target-project> <task-id> --kind git_snapshot --json
python scripts/taskgov.py review receipt add --repo <target-project> <task-id> --reviewer <reviewer-a> --kind independent --verdict pass --summary "No blocking findings" --json
python scripts/taskgov.py review receipt add --repo <target-project> <task-id> --reviewer <reviewer-b> --kind independent --verdict pass --summary "No blocking findings" --json
git commit -m "<project-approved message>"
python scripts/taskgov.py task edit --repo <target-project> <task-id> --status done --verification-complete --review-complete --completion-commit-hash <hash> --json
```

`review target set --kind git_snapshot` accepts no `--revision`. It reads the
canonical current `HEAD` and stage-0 index entries without changing Git.
Unstaged and untracked content is excluded. At completion, the commit must have
exactly one parent equal to the stored base and a tree fingerprint identical to
the reviewed snapshot. Root and merge commits are unsupported for this path.
If the reviewed content changes, set a new snapshot target and obtain fresh
reviews. A matching commit needs only the original two Tier 2 judgments; the
base/tree binding is deterministic.

After a task is `done`, all task and review writes are rejected except this
exact reason-required reopen:

```powershell
python scripts/taskgov.py task edit --repo <target-project> <task-id> --status in_progress --reopen-reason "<sanitized reason>" --json
```

Reopen preserves historical evidence but requires fresh verification, review,
and completion evidence before the task can be completed again.

## Local Handoff Workflow

Version `0.4.0` adds a Task-DB-local outbox for out-of-scope discoveries.
After classifying a finding once as current Task, blocker, or handoff, record
the last category before continuing:

```powershell
python scripts/taskgov.py handoff record --repo <target-project> <task-id> --summary "Concise discovery" --rationale "Outside current acceptance" --json
python scripts/taskgov.py handoff list --repo <target-project> --json
python scripts/taskgov.py handoff show --repo <target-project> <handoff-id> --json
```

This release has no Issue adapter, receiver detection, delivery, or
`handoff sync`; every successful record remains locally rediscoverable as
`pending_handoff`. Exact replay is idempotent. A distinct occurrence requires
an explicit stable `--occurrence-id`. Pending rows do not change task
selection, task events, timestamps, review, or completion.

Only explicit user direction may withdraw a never-attempted pending record:

```powershell
python scripts/taskgov.py handoff withdraw --repo <target-project> <handoff-id> --reason "Handled outside Task Skill" --json
```

The release artifact stores no Issue priority/lifecycle/resulting-task field,
claim token output, raw logs, secrets, private prompts, stack traces, or large
diffs. A failed local record is never reported durable and returns
`handoff_not_persisted` after its bounded local persistence retry is exhausted.

## Viewer Runtime State

After installation, an explicitly requested `web export` writes the default
viewer to:

```text
<installed-skill-root>\state\projects\<project-id>\viewer\task-viewer.html
```

Use an explicit `--output` only after the user approves that complete path; its
parent must already exist. Snapshot v3 continues to serve schemas v5-v7,
includes typed completion and bounded structured review evidence, carries the
actual source schema, and omits the internal
`review_target_base_revision`, handoff rows, and handoff summaries. The HTML is
stale until the user requests regeneration. It has no server, live database
refresh, browser editing, or automatic browser launch. Generated viewers
remain runtime state and must not be added to the release artifact.

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
   artifact includes `assets/task-viewer.template.html`, `viewer.py`, and
   `git_snapshot.py`, and `handoffs.py`.
6. Confirm generated `state/`, SQLite files, generated `task-viewer.html`, root
   copied references, logs, and caches are ignored and absent from the artifact.
7. Confirm `task-governance-tool/SKILL.md` frontmatter contains only `name` and
   `description`, and the `name` matches the folder.
8. Confirm `taskgov --version` reports `0.4.0`, storage reports schema v7, and
   the Skill, workflow, CLI contracts, README, and this note describe the same
   snapshot/reopen/local-handoff behavior.

## Publication Notes

Initial publication should avoid network services and cloud dependencies. The
skill is local-first and should remain usable offline.

Versioning follows the runtime package version in
`task-governance-tool/scripts/task_governance_tool/__init__.py`. Release notes
should name current/next task inspection, pause/resume, sequential transition
guards, typed completion evidence with read-only Git validation, structured
review gates, exact paused counts, the bounded current-status filter, the
advisory paused-work warning, schema v6 migration, deterministic staged-snapshot
completion binding, done/reopen safety, schema-v7 local handoff, and
snapshot-v3 offline Viewer export. The implemented TG-M12.1 version `0.4.0`
adds schema v7 and local-only handoff behavior beyond the TG-M11
version `0.3.0` completion-integrity release, historical `0.2.0` TG-M8 release
candidate, and the `0.1.0` trial.
