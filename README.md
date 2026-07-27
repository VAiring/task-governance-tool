# task-governance-tool

`task-governance-tool` is a local-first Codex Skill and stdlib Python CLI for
keeping long-running work resumable, reviewable, and bounded. It stores local
task state without replacing the target project's `AGENTS.md`, specifications,
design documents, tests, or current user decisions.

Release `0.8.0` uses SQLite schema v13 and Viewer snapshot v3.

## Install

Python 3.12 or newer on Windows is the supported runtime. Windows is the
CI-verified platform; Linux and macOS are not claimed as supported.

Install one physical copy of the installable `task-governance-tool/` folder for
each governed project at exactly:

```text
<target-project>\.agents\skills\task-governance-tool
```

Only this project-scoped physical layout is supported for ordinary use. Show
the exact destination and obtain approval before installing or replacing it.
The release artifact and package creation process never initialize project
state.

For a Git-managed target, add this narrow, root-anchored ignore rule before
setup:

```gitignore
/.agents/skills/task-governance-tool/state/
```

The rule covers only generated state owned by this Skill. A non-Git directory
is also a valid governed project and needs no Git ignore check.

From the target-project root, preview and then perform setup:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py setup --read-only --json
python .agents/skills/task-governance-tool/scripts/taskgov.py setup --json
```

`setup` is the only initializer and migrator. It also performs the one-way
opt-in to project-local maintenance and publishes or repairs the canonical
offline Viewer. It is noninteractive and idempotent. If the canonical DB is
missing while a valid managed generation remains, setup recovers the newest
valid same-project generation before normal migration and Viewer repair. It
does not add a recovery command or accept a recovery path.

If invoking the CLI from inside the installed Skill directory, pass the target
directory explicitly:

```powershell
python scripts/taskgov.py --repo <target-project> setup --json
```

Omitting `--repo` means the current directory; the CLI does not search for a
Git root. An explicit repository argument is therefore required from the Skill
directory. The governed directory itself does not need to be a Git repository.

`doctor` is the sole diagnostic and is always read-only:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py doctor --json
```

It reports package integrity, setup readiness, compact task and handoff counts,
and bounded maintenance status. It never initializes, migrates, repairs,
renders, backs up, runs project tests, or inspects Git. It is optional and is
not a prerequisite for setup or normal task work.

## Minimal Task Workflow

Register only work already approved by the user or an approved roadmap:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py task add --title "Example task" --json
```

At a later session boundary, first rediscover current work:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py task current --compact --json
```

If `task current` returns an `in_progress` or `review_pending` row, resume the
first such row in returned order. Otherwise select ready work; returned
`paused` and `blocked` rows remain visible but do not suppress unrelated ready
selection:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py task next --compact --json
```

Always inspect the resumed or selected task:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py task show <task-id> --json
```

Only when a ready task was selected, start it:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py task edit <task-id> --status in_progress --json
```

`task show` is the mandatory detailed read before work. It also exposes whether
the optional Effort Advisory is enabled, so the default-off flow needs no extra
LLM choice or command. `task current` rediscovers paused, blocked,
review-pending, and in-progress work.

At a genuine continuation boundary, an optional typed checkpoint records only
the bounded summary, next action, and unresolved risks:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py task checkpoint <task-id> --summary "Verified current slice" --next-action "Continue with the next acceptance item" --json
```

Record an out-of-scope discovery immediately without changing the current
task's acceptance criteria:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py handoff record <task-id> --summary "Concise sanitized discovery" --json
```

The record remains a local `pending_handoff`. This release does not triage,
prioritize, synchronize, or create external Issues.

## Review And Completion

For a review-before-commit Git workflow, stage exactly the intended project
changes through the project's own Git process, capture the staged target, and
prepare one bounded review packet:

```powershell
git add <intended-project-paths>
python .agents/skills/task-governance-tool/scripts/taskgov.py review target set <task-id> --kind git_snapshot --json
python .agents/skills/task-governance-tool/scripts/taskgov.py review prepare <task-id> --json
python .agents/skills/task-governance-tool/scripts/taskgov.py review receipt add <task-id> --reviewer <reviewer-a> --kind independent --verdict pass --summary "No blocking findings" --json
python .agents/skills/task-governance-tool/scripts/taskgov.py review receipt add <task-id> --reviewer <reviewer-b> --kind independent --verdict pass --summary "No blocking findings" --json
git commit -m "<project-approved message>"
python .agents/skills/task-governance-tool/scripts/taskgov.py task complete <task-id> --completion-evidence-kind git_commit --completion-revision <hash> --verification-complete --review-complete --json
```

The staged snapshot excludes unstaged and untracked content. The completion
commit must have exactly one parent equal to the captured base and the same
tree. Meaningful target changes require a new target and fresh receipts. The
Skill validates Git evidence read-only; it never stages, commits, branches,
pushes, opens a PR, or creates an Issue.

The packet tells each reviewer how to inspect the exact target rather than
ambient `HEAD` or worktree content. The independent reviewer returns the
verdict and findings; the trusted parent/orchestrator records their sanitized
result with the shown receipt command and existing finding commands. Reviewer
keys enforce distinct strings only and are not authentication or identity
proof.

Tier 1 normally requires one independent PASS. Tier 2 normally requires two
distinct independent PASS receipts for the same target generation and blocks
completion while a high or medium finding remains unresolved. Use
`task complete --check` only when an explicit read-only completion check is
useful; it is not part of the normal success path.

A completed task is locked. Reopen requires an explicit reason, preserves
historical events, and requires fresh verification, target, receipts, and
completion evidence:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py task edit <task-id> --status in_progress --reopen-reason "<sanitized reason>" --json
```

## Local Maintenance

Successful `setup` stores the backup policy in the project-local SQLite state.
Defaults are 30 minutes since the last successful managed backup and 3 retained
generations. Only an explicit setup call may change them:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py setup --backup-interval-minutes 60 --backup-generations 5 --json
```

Omitted options preserve an existing policy. Supported ranges are 1-1,440
minutes and 1-20 generations. No second configuration file is created.

After opt-in, each eligible successful state mutation closes its SQLite write
before running bounded same-process maintenance. The canonical Viewer is
updated first, followed by at most one due backup attempt. Viewer maintenance
renders at most twice to absorb one concurrent change. Backup and Viewer
failures preserve the primary command result, keep maintenance due, and are
reported only as bounded sanitized warnings.

There is no daemon, timer, background process, queue, service, browser launch,
custom Viewer destination, or maintenance command. The generated Viewer and
managed backups remain projections/runtime artifacts under the ignored Skill
`state/` directory. Viewer snapshot v3 reads source schemas 5 through 13,
contains bounded sanitized task/review history, has no write controls or
network dependency, and requires a normal browser reload to observe a newly
published page.

## Public Commands

Release 0.8.0 exposes exactly these 20 command leaves:

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

Applicable commands accept `--repo`, `--json`, and `--read-only`; the root also
accepts `--version`. Storage paths and maintenance internals are not public CLI
choices.

## Privacy And Scope

The Skill stores sanitized task metadata, compact events, completion evidence,
review evidence, handoffs, optional Contracts, checkpoints, and bounded
maintenance facts. It does not store raw stdout/stderr, stack traces,
environment dumps, full prompts or conversations, authorization material,
large raw diffs, or raw review transcripts.

It is local-first and uses no network service. It does not mutate target-project
files or Git state, run project-specific verification automatically, or create
external Issues/PRs. SQLite remains helper state; governing project documents
and current user decisions remain authoritative.

## Development Checks

Before publication, run at least:

```powershell
python -m unittest discover -s tests
python task-governance-tool\scripts\taskgov.py --help
python task-governance-tool\scripts\taskgov.py --version
git diff --check
```

Also validate an isolated physical project-scoped package with `doctor`, verify
the exact 20-leaf help surface, and confirm generated `state/`, SQLite files,
Viewer HTML, backups, logs, caches, and root copied references are absent from
the release artifact.

## Project Docs

- `docs/specification.md`: product contract.
- `docs/design.md`: implementation design and boundaries.
- `docs/implementation-roadmap.md`: approved execution units and review gates.
- `docs/release-install.md`: release artifact and installation decision.
