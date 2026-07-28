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
and the three one-level Skill references. The manifest is the exact inventory
and digest authority for packaged core files.

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

For a Git-managed target project, ensure the canonical Skill state directory
is effectively ignored. This narrow target-local rule is recommended:

```gitignore
/.agents/skills/task-governance-tool/state/
```

An enclosing worktree rule for the same directory is also accepted. Do not
recommend repository-wide extension globs. The one directory rule contains
this Skill's database, sidecars, backups, locks, and generated Viewer without
hiding unrelated project fixtures or assets. Non-Git governed directories are
valid and do not require an ignore file.

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

Omitting `--repo` selects the current directory and never re-roots it to an
enclosing Git worktree. The target directory must exist, but it need not be a
Git repository.

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
maintenance lock, runs project verification, or changes the target project.
For a Git-candidate target, only its single bounded effective-ignore preflight
may inspect Git. Doctor is optional and is not a setup or normal-loop
prerequisite.

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

Taskgov starts no daemon, thread, timer, detached process, scheduler, queue,
service, browser, custom-destination operation, public maintenance command, or
separate model decision. Handoff-only writes may make backup due but do not
change the Viewer generation. Setup publishes the Viewer directly.

The Viewer is a self-contained, read-only `file://` projection under the
ignored package state. Snapshot v3 accepts source schemas v5-v13 and omits
storage paths, maintenance internals, checkpoint content, handoffs, raw
evidence, environment data, and secrets. It performs no network request and
provides no database or task write control.

An optional browser-only refresh profile may exist at the physical installed
package's `config/viewer.json`. Taskgov never creates or edits it, and the file
must not be shipped in a release artifact or listed in the core manifest.
Absence is valid and leaves automatic refresh disabled
with no timer. A present file must be no larger than 16,384 bytes and contain
only strict UTF-8 JSON schema 1, profile `visibility-refresh-v1`, and an integer
`refresh_interval_seconds` from 5 through 3,600. Links, reparse points,
non-regular objects, replacement races, invalid encoding/JSON, and non-exact
fields fail closed without exposing path or OS details.

The resolved setting is embedded on the next Viewer-relevant publication or
explicit setup. A valid setting schedules at most one timeout only in an
already opened visible `file://` page and requests at most one same-document
reload per loaded page. It does not launch a browser, watch SQLite, use a
network, or use Web Storage. Only immediately before that automatic reload, a
fixed at-most-4,096-byte, five-minute envelope in the current History entry may
preserve non-search filters, selected Task, fixed-control focus, and document
scroll. The reloaded page clears owned state before validation, leaves
an unrelated `history.state` payload untouched, and changes neither URL nor
history length. Five minutes is the restore acceptance limit rather than a
physical-erasure guarantee; browser-managed state may survive session restore
but an owned envelope is consumed before validation. Invalid profile preview is
no-write `repair_required`; actual setup uses
`setup_incomplete`, while routine mutations preserve their primary success and
last-good Viewer and append `viewer_refresh_failed` for this Viewer/config
failure. An independently due backup attempt and its existing warning remain
unchanged.

## Public CLI Surface

The release exposes exactly these 20 command leaves:

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

An enabled `task effort` result returns `suggested_action=continue` or the
single non-blocking `suggested_action=reconcile_scope` route. The latter loads
`references/reconciliation.md` once for the whole result. Initial failure
integrity remains in the Skill itself:
failed verification or blocking review prevents affected completion but not
safe authorized repair or unrelated ready work, and tests are never weakened
merely to obtain PASS. Without new evidence, two materially equivalent failed
repairs or review-remediation cycles prohibit a third equivalent cycle. The
comparison is session-local, creates no SQLite counter or new command, and
resets in a fresh session; durable Task, handoff, finding, and receipt state
remains available through normal rediscovery.

For review before the completion commit, stage exactly the intended files,
set the target with `review target set <task-id> --kind git_snapshot`, and use
the single bounded packet from `review prepare`. Unstaged and untracked files
remain outside that target. The packet adds one deterministic target-kind
instruction so reviewers inspect the exact stored index, commit, fingerprint-
bound material, or external revision rather than ambient content. The
independent reviewer returns the result; the trusted parent/orchestrator
records its sanitized receipt/findings. Reviewer keys prove distinct strings,
not identity.

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

1. Confirm all M14.1-M14.7 and reduced TG-M16 acceptance and required Tier 2
   reviews passed on their exact revisions.
2. Run the complete offline test suite and `git diff --check`.
3. Verify root and group help expose exactly the 20 leaves above.
4. Verify unknown removed surfaces and raw path options fail before any
   project, Git, or SQLite observation.
5. Confirm `taskgov --version` reports `0.8.0`, runtime schema is v13, and
   Viewer snapshot v3 accepts source schemas v5-v13.
6. Validate an isolated physical project-scoped install: package status is
   clean in `doctor`, setup preview is no-write, setup succeeds, and a repeated
   setup is idempotent.
7. Run the installed-Skill self-containment, realistic setup-to-completion
   smoke tests, and the TG-M16 fresh-session repair, review, and rediscovery
   pressure scenarios.
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
unchanged while its accepted source schemas expand through v13. Reduced loop
discipline routes only deterministically exceeded Effort results to one
non-blocking reconciliation episode and bounds repeated repair in session-local
guidance without adding a command, schema field, persisted counter, or normal
green-path stop.
