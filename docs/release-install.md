# Release And Install Decision

This note defines the release artifact and supported installation boundary for
`task-governance-tool` 0.8.0. It does not authorize installing or overwriting a
Skill in any project.

## Release Identity

- Package version: `0.8.0`
- SQLite schema: v13
- Viewer snapshot: v3, with source-schema compatibility from v5 through v13
- Supported runtime: Python 3.12 or newer on Windows
- Verified platform: Windows

Linux and macOS are not claimed as supported in this release.

## Publication Decision

Publish the reviewed source repository as a Git tag and publish a separate
installable archive whose root is the repository's
`task-governance-tool/` directory.

The installable package contains the co-located release manifest, `SKILL.md`,
display metadata, bundled Viewer template, CLI entry point and runtime modules,
and the two one-level Skill references. The manifest is the exact inventory and
digest authority for packaged core files.

The archive must exclude:

- generated `state/`, Viewer HTML, managed backups, SQLite databases, and
  SQLite sidecars;
- root `references/`, tests, fixtures, development-only documents, and local
  scratch output;
- caches, logs, temporary files, environment files, secrets, and editor files.

Creating an archive is side-effect free. It does not initialize, migrate, copy,
or inspect a target project's local state.

## Supported Installation

Install one physical package copy per governed project at exactly:

```text
<target-project>\.agents\skills\task-governance-tool
```

No other ordinary stateful layout is supported. Before installation or update,
show the complete destination and obtain explicit user approval. Do not
overwrite an existing package without a separate update decision, and do not
delete its generated project-local `state/` while replacing packaged core
files.

For a Git-managed target project, add only this root-anchored ignore rule:

```gitignore
/.agents/skills/task-governance-tool/state/
```

Do not recommend repository-wide extension globs. The one directory rule
contains this Skill's database, sidecars, backups, locks, and generated Viewer
without hiding unrelated project fixtures or assets. Non-Git governed
directories are valid and do not require an ignore file.

From the target-project root, preview setup before the explicit write:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py setup --read-only --json
python .agents/skills/task-governance-tool/scripts/taskgov.py setup --json
```

When running from inside the installed Skill directory, supply the target
project explicitly:

```powershell
python scripts/taskgov.py --repo <target-project> setup --json
```

Omitting `--repo` selects the current directory and never searches for a Git
root. The target directory must exist, but it need not be a Git repository.

## Setup And Upgrade Contract

`setup` is the only public initializer and migrator. It also performs the
one-way opt-in to bounded local maintenance and directly publishes or repairs
the canonical Viewer. It is explicit, noninteractive, idempotent, and limited
to the supported physical project-scoped package.

If the canonical DB is missing but canonical managed generations remain,
setup validates only same-project artifacts, selects the newest valid
generation deterministically, restores it without overwriting any existing
canonical DB, and continues normal migration/configuration/Viewer repair.
Invalid, foreign, linked, and unrecognized artifacts are unchanged. If no
valid same-project managed candidate exists, setup fails instead of creating
empty task state. An orphan rollback journal for the missing canonical DB also
fails closed and remains untouched. There is no public recovery command or
recovery path option.

Fresh setup and migration default to:

- backup interval: 30 minutes after the last successful managed backup;
- retained managed generations: 3.

The policy is stored with project state in SQLite; no JSON, TOML, or other
second configuration file is created. Explicit setup options may select
1-1,440 minutes and 1-20 generations:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py setup --backup-interval-minutes 60 --backup-generations 5 --json
```

Omitted options preserve already configured values. A policy-only replay with
omitted or equal values performs no write. Once enabled, maintenance has no
public disable surface.

Before migration, setup creates one validated managed backup while holding the
shared fail-fast artifact lock. Migration from supported older schemas is
transactional, ordered, idempotent, and never a downgrade. It preserves task
and event identity, completion/review history, handoffs, Contracts, effort
metadata, and checkpoints through schema v13. An older runtime rejects a newer
schema.

Installation alone and ordinary task commands never migrate. After replacing
packaged core files while preserving local state, run `setup`; it performs any
required migration and Viewer repair. A failed migration backup prevents the
migration.

## Doctor Contract

`doctor` is the sole diagnostic:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py doctor --json
```

It is inherently read-only and always reports
`suggested_action="continue"` for recognized advisory conditions. It observes
package integrity separately, then—when state is readable—uses one
lock-respecting SQLite read transaction for project, task, handoff, backup, and
Viewer status.

Doctor never initializes, migrates, backs up, renders, repairs, acquires a
maintenance lock, inspects Git, runs project verification, or changes the
target project. It is optional and is not a setup or normal-loop prerequisite.

## Runtime Maintenance

After setup opt-in, every eligible successful state mutation closes its SQLite
write before the fixed same-process maintenance coordinator runs:

1. refresh the canonical Viewer when its source generation is ahead;
2. attempt at most one backup when the stored interval is due.

Viewer publication permits one initial render and at most one follow-up render.
Backup rotation keeps only the applied configured generation count. Both use
zero-wait OS advisory locks and bounded sanitized outcomes. Contention or
failure preserves the primary command result and leaves the maintenance stage
due for the next eligible mutation.

There is no daemon, thread, timer, detached process, scheduler, queue, service,
browser launch, custom destination, public maintenance command, or separate
model decision. Handoff-only writes may make backup due but do not change the
Viewer generation. Setup publishes the Viewer directly.

The Viewer is a self-contained, read-only `file://` projection under the
ignored package state. Snapshot v3 accepts source schemas v5-v13 and omits
storage paths, maintenance internals, checkpoint content, handoffs, raw
evidence, environment data, and secrets. It performs no network request and
provides no database or task write control. A browser reload is the only
refresh action outside taskgov.

## Public CLI Surface

The release exposes exactly these 20 leaves:

1. `taskgov setup`
2. `taskgov doctor`
3. `taskgov task add`
4. `taskgov task list`
5. `taskgov task next`
6. `taskgov task current`
7. `taskgov task effort`
8. `taskgov task show`
9. `taskgov task edit`
10. `taskgov task complete`
11. `taskgov task checkpoint`
12. `taskgov handoff record`
13. `taskgov handoff list`
14. `taskgov handoff show`
15. `taskgov handoff withdraw`
16. `taskgov review prepare`
17. `taskgov review target set`
18. `taskgov review receipt add`
19. `taskgov review finding add`
20. `taskgov review finding resolve`

Applicable leaves retain `--repo`, `--json`, and `--read-only`; the root retains
`--version`. Storage and generated-artifact paths are internal implementation
details, not public CLI choices. Unknown or removed commands/options fail at
the parser boundary before package, project, Git, or SQLite resolution.

The normal no-finding Tier 2 Task flow uses at most nine governance subprocess
calls with the default-off Effort Advisory, or ten when an enabled valid
profile mechanically adds `task effort`. Doctor, checkpoint, and completion
check are optional and absent from the standard success path.

For review before the completion commit, stage exactly the intended files,
set the target with `review target set <task-id> --kind git_snapshot`, and use
the single bounded packet from `review prepare`. Unstaged and untracked files
remain outside that target.

## Safety And Privacy

`taskgov` may write only canonical generated Skill state after setup and the
explicit task-state operation requested by the caller. It does not edit target
project source, stage or write Git, open a browser, create an Issue or PR, or
use the network.

Default retention excludes raw stdout/stderr, stack traces, environment dumps,
full prompts or conversations, authorization material, raw provider bodies,
large diffs, raw review transcripts, and secrets. The Viewer and diagnostic
envelopes expose bounded allow-listed projections only.

SQLite is helper state, not authority over project decisions. The target
project's governing documents and current user decisions remain authoritative.

## Pre-Release Checks

Before creating the 0.8.0 artifact:

1. Confirm all M14.1-M14.7 acceptance and required Tier 2 reviews passed on
   their exact revisions.
2. Run the complete offline test suite and `git diff --check`.
3. Verify root and group help expose exactly the 20 leaves above.
4. Verify unknown removed surfaces and raw path options fail before any
   project, Git, or SQLite observation.
5. Confirm `taskgov --version` reports `0.8.0`, runtime schema is v13, and
   Viewer snapshot v3 accepts source schemas v5-v13.
6. Validate an isolated physical project-scoped install: package status is
   clean in `doctor`, setup preview is no-write, setup succeeds, and a repeated
   setup is idempotent.
7. Run the installed-Skill self-containment and realistic
   setup-to-completion smoke tests.
8. Confirm the archive manifest includes every packaged core file with current
   digests and package version `0.8.0`.
9. Confirm generated state, SQLite files and sidecars, backups, locks, Viewer
   HTML, root references, logs, caches, and scratch output are absent from the
   artifact.
10. Confirm `SKILL.md`, display metadata, workflow, CLI contracts, README, this
    note, executable help, and release manifest describe one synchronized
    revision.

Publishing, pushing, creating a PR, or dispatching external CI requires
separate explicit authorization.

## Release Summary

Version 0.8.0 consolidates setup and diagnostics, adds typed checkpoints and a
bounded Review Packet, moves backup and Viewer upkeep behind deterministic
post-commit maintenance, removes storage and Viewer administration from the
public CLI, and migrates local state through schema v13. Viewer snapshot v3 is
unchanged while its accepted source schemas expand through v13.
