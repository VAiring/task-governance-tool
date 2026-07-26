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

The skill must be installed as one physical copy per governed project, not as a
user-wide shared skill, symbolic link, or Windows junction. A separate
project-scoped copy keeps task state attached to the project whose tasks it
manages.

The supported runtime baseline is Python 3.12 or later on Windows. Windows is
the CI-verified platform. Linux and macOS are unverified and have no support
claim in this release.

## Release Artifact Contents

The installable artifact should contain:

```text
task-governance-tool/
  release-manifest.json
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
      contracts.py
      effort.py
      self_status.py
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

For normal stateful use, install a physical artifact copy into the governed
target project:

```text
<target-project>\.agents\skills\task-governance-tool
```

This project-scoped location is the Codex skill discovery path for a project.
It also keeps the default database path project-local:

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
/.agents/skills/task-governance-tool/state/
```

From the target-project root, inspect and initialize explicitly:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py self status --read-only --json
python .agents/skills/task-governance-tool/scripts/taskgov.py db status --json
python .agents/skills/task-governance-tool/scripts/taskgov.py db init --json
python .agents/skills/task-governance-tool/scripts/taskgov.py db status --json
```

If a command is launched from inside the installed Skill directory, pass
`--repo <target-project>` explicitly. Omitting it always selects the current
directory and never searches for a Git root; non-Git governed directories are
valid. Stateful commands reject a linked Skill install path before creating
state.
`self status` diagnoses that unsupported layout as `unknown` while retaining
its fixed `suggested_action=continue`.

`self status` checks only the installed package and does not touch SQLite,
Git, or the network. Only `db init` creates or migrates the database. Version
`0.7.0` uses schema v9
and migrates supported schema-v2 through v8 databases through the explicit
ordered migration path while retaining task/event IDs, completion hashes, and
structured review history. Schema v7 adds an empty local handoff outbox without
rewriting existing tasks or evidence. Schema v8 adds revision-zero pointers and
an empty immutable Contract table without rewriting existing task or handoff
history. Schema v9 adds empty Effort Advisory basis/activity metadata and a
zero project activity generation; it does not rewrite existing tasks, events,
completion evidence, reviews, handoffs, or Contracts. Skill installation or
an ordinary task command does not migrate a database. Back up the project-local
database before updating the skill, then run `db init` explicitly. Migrations
are transactional, repeatable, and never downgrade a database; an older
runtime rejects a newer schema. An incomplete migration history is not repaired
by inference:
`db init` returns `migration_required` without mutation so the operator can
restore a valid backup or inspect the history.

Project identity remains derived from the canonical absolute target path.
Moving or renaming the governed directory therefore changes its default
identity and can make existing state appear uninitialized. This release does
not provide automatic relocation, a relocation command, or a project UUID.

## Git Snapshot Completion Workflow

Version `0.3.0` supports review before the project creates its completion
commit. Stage only the intended project changes through the project's own Git
workflow, capture a snapshot target without a caller revision, collect the
required reviews, create the project commit, and then complete the task:

```powershell
git add <intended-project-paths>
python .agents/skills/task-governance-tool/scripts/taskgov.py review target set <task-id> --kind git_snapshot --json
python .agents/skills/task-governance-tool/scripts/taskgov.py review receipt add <task-id> --reviewer <reviewer-a> --kind independent --verdict pass --summary "No blocking findings" --json
python .agents/skills/task-governance-tool/scripts/taskgov.py review receipt add <task-id> --reviewer <reviewer-b> --kind independent --verdict pass --summary "No blocking findings" --json
git commit -m "<project-approved message>"
python .agents/skills/task-governance-tool/scripts/taskgov.py task edit <task-id> --status done --verification-complete --review-complete --completion-commit-hash <hash> --json
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
python .agents/skills/task-governance-tool/scripts/taskgov.py task edit <task-id> --status in_progress --reopen-reason "<sanitized reason>" --json
```

Reopen preserves historical evidence but requires fresh verification, review,
and completion evidence before the task can be completed again.

## Local Handoff Workflow

Version `0.4.0` adds a Task-DB-local outbox for out-of-scope discoveries.
After classifying a finding once as current Task, blocker, or handoff, record
the last category before continuing:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py handoff record <task-id> --summary "Concise discovery" --rationale "Outside current acceptance" --json
python .agents/skills/task-governance-tool/scripts/taskgov.py handoff list --json
python .agents/skills/task-governance-tool/scripts/taskgov.py handoff show <handoff-id> --json
```

This release has no Issue adapter, receiver detection, delivery, or
`handoff sync`; every successful record remains locally rediscoverable as
`pending_handoff`. Exact replay is idempotent. A distinct occurrence requires
an explicit stable `--occurrence-id`. Pending rows do not change task
selection, task events, timestamps, review, or completion.

Only explicit user direction may withdraw a never-attempted pending record:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py handoff withdraw <handoff-id> --reason "Handled outside Task Skill" --json
```

The release artifact stores no Issue priority/lifecycle/resulting-task field,
claim token output, raw logs, secrets, private prompts, stack traces, or large
diffs. A failed local record is never reported durable and returns
`handoff_not_persisted` after its bounded local persistence retry is exhausted.
If input is rejected by the privacy guard, never repeat, quote, log, store, or
forward the rejected raw content. The packaged workflow permits at most one
new `handoff record` attempt using a concise sanitized abstraction; a second
privacy rejection ends that recovery attempt with only the fixed error.

## Optional Task Contract Workflow

Version `0.5.0` adds an optional immutable Contract copied from already
explicit scope and acceptance. It does not ask the user for missing fields or
infer activation from task size. Use the documented `--contract-*` group on
`task add`, or on an exact revision-zero `ready|blocked -> in_progress`
transition. Later semantic revisions are Contract-only and require explicit
later authority plus a concise reason. Exact replay is write-free.

Contract revisions remain SQLite helper state subordinate to governing docs and
user decisions. They are excluded from list/current/next and Viewer snapshots.
New handoffs capture the current Contract revision in their identity; existing
schema-v7 handoffs remain revision zero. There is no expected-revision,
signature, Issue, or general workflow engine in this release.

## Optional Effort Advisory

Effort Advisory remains disabled unless the consuming project explicitly adds
`config/effort-advisory.json` to its installed Skill copy with
`schema_version: 1`, `profile: "informational-v1"`, and `enabled: true`.
The optional `thresholds` object accepts only `changed_files`,
`changed_lines`, `changed_modules`, `contract_revisions`, and `handoffs`.
Thresholds are non-negative integers and are exceeded only when the observed
value is greater than the configured value. Unknown fields or invalid values
disable the profile and produce a bounded diagnostic; task work still
continues.

When enabled, an existing transition into `in_progress` may best-effort capture
a clean or dirty Git basis in schema v9. Capture failure never rejects the
Task transition. `task effort <task-id>` is offline and read-only; it reports
unknown rather than guessing when Git coverage or exclusive-task attribution
is unreliable. It never records an acknowledgement, changes Task or handoff
state, asks a question, or creates a stop.

## Local Package Self-Status

Version `0.7.0` adds the offline package-local command:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py self status --read-only --json
```

It compares every packaged core file with `release-manifest.json` and reports
`clean`, `modified`, or `unknown`, installed version, manifest-declared release
origin, and at most 20 relative changed paths. Root `config/`, `adapters/`,
generated `state/`, and Python caches are explicitly outside core. Added files
elsewhere are local core modifications. `--repo` and `--db` are accepted for
CLI compatibility but are not read or resolved.

Every result is successful advisory output with
`suggested_action=continue`. Missing or invalid manifests, version mismatch, or
an incomplete bounded inspection are `unknown`; they do not stop task work.
The command does not restore, update, download, install, contact GitHub,
inspect Git, write a cache/database, or create an Issue, PR, or handoff.
The declared origin and co-located hashes are useful for accidental local
drift, but are not a signature: coordinated replacement of core and manifest
can evade the check.

## Viewer Runtime State

After installation, an explicitly requested `web export` writes the default
viewer to:

```text
<installed-skill-root>\state\projects\<project-id>\viewer\task-viewer.html
```

Use an explicit `--output` only after the user approves that complete path; its
parent must already exist. Snapshot v3 continues to serve schemas v5-v9,
includes typed completion and bounded structured review evidence, carries the
actual source schema, and omits the internal
`review_target_base_revision`, handoff rows, handoff summaries, and all
Contract fields/revisions. The HTML is stale until the user requests
regeneration. It has no server, live database refresh, browser editing, or
automatic browser launch. Generated viewers remain runtime state and must not
be added to the release artifact.

User-wide and linked Skill locations are not public stateful operating modes.
Use only the physical project-scoped layout documented above.

## Pre-Release Checks

Before creating a release artifact:

1. Confirm the worktree is clean.
2. Run the full offline test suite.
3. Run the fallback skill self-check or official skill validation helper when
   available.
4. Run the installed skill self-containment smoke test.
5. Confirm an isolated installed copy reports `self status=clean`, can run
   `web export`, and that the artifact includes `release-manifest.json`,
   `assets/task-viewer.template.html`, `viewer.py`, `git_snapshot.py`,
   `handoffs.py`, `contracts.py`, `effort.py`, and `self_status.py`.
6. Confirm generated `state/`, SQLite files, generated `task-viewer.html`, root
   copied references, logs, and caches are ignored and absent from the artifact.
7. Confirm `task-governance-tool/SKILL.md` frontmatter contains only `name` and
   `description`, and the `name` matches the folder.
8. Confirm `taskgov --version` reports `0.7.0`, storage reports schema v9, and
   the Skill, workflow, CLI contracts, README, and this note describe the same
   snapshot/reopen/local-handoff/Contract/Effort-Advisory/self-status behavior.

## Publication Notes

Initial publication should avoid network services and cloud dependencies. The
skill is local-first and should remain usable offline.

Versioning follows the runtime package version in
`task-governance-tool/scripts/task_governance_tool/__init__.py`. Release notes
should name current/next task inspection, pause/resume, sequential transition
guards, typed completion evidence with read-only Git validation, structured
review gates, exact paused counts, the bounded current-status filter, the
advisory paused-work warning, schema v6 migration, deterministic staged-snapshot
completion binding, done/reopen safety, schema-v7 local handoff, schema-v8
optional Task Contracts, schema-v9 default-off Effort Advisory, and snapshot-v3
offline Viewer export. Version `0.7.0` adds offline local package self-status
without changing schema v9 or Viewer snapshot v3. Version `0.6.0` added schema
v9 and the read-only advisory beyond version `0.5.0`, which added schema v8 and
Task Contracts.
TG-M12.1 version `0.4.0` added schema v7 and local-only handoff behavior.
Earlier history includes the TG-M11 version `0.3.0` completion-integrity
release, historical `0.2.0` TG-M8 release candidate, and the `0.1.0` trial.
