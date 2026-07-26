---
name: task-governance-tool
description: Project-scoped local-first task tracking for Codex using the bundled taskgov CLI and skill-local SQLite state. Use when planning or registering explicit tasks, initializing or inspecting task state, rediscovering current work, selecting next actionable work, pausing or handling blockers, durably handing off out-of-scope discoveries, updating or completing tasks with structured review and completion evidence, or creating or regenerating a user-requested offline static Task Viewer.
---

# Task Governance Tool

Use this skill to work from compact local task state instead of loading large
task-status documents. Treat the target project's `AGENTS.md`, specs, design
docs, tests, and user decisions as the source of truth; SQLite is only a local
task-state helper.

Install a separate copy per governed project, normally at
`.agents/skills/task-governance-tool`. Do not use a user-wide copy as the normal
task manager for unrelated projects.

## Quick Start

Run the bundled CLI from the installed skill folder:

```powershell
python scripts/taskgov.py db status --repo <target-project> --json
python scripts/taskgov.py task current --repo <target-project> --json
python scripts/taskgov.py task current --repo <target-project> --status paused --json
python scripts/taskgov.py task next --repo <target-project> --json
python scripts/taskgov.py task show --repo <target-project> <task-id> --json
python scripts/taskgov.py handoff list --repo <target-project> --json
```

First-use flow:

1. Run `db status` without creating files.
2. After project-scoped installation, verify that generated `state/` is ignored.
   If the user intends to use local tracking, run `db init`. Package creation
   itself never initializes a target database.
3. Register only explicit user-approved tasks with `task add`; do not import
   large task files or invent dependency graphs.
4. Use `task current` to rediscover started, paused, blocked, or review-pending
   work. If `db status` reports paused work or `task next` emits
   `paused_tasks_present`, use `task current --status paused`. Use `task next`
   only for new ready work, then inspect with `task show`.

Use `--db <path>` only when explicitly required. The default is
`state/projects/<project-id>/taskgov.sqlite` under this installed skill. If the
skill came from a global install, do not initialize state until the user
confirms that non-standard setup or installs a project-scoped copy.

## Static Task Viewer

Create or regenerate the offline viewer only when the current user explicitly
asks for that HTML artifact. Inspection alone authorizes CLI reads, not an HTML
write.

Preview without writing:

```powershell
python scripts/taskgov.py web export --repo <target-project> --read-only --json
```

After explicit user intent, generate the default snapshot:

```powershell
python scripts/taskgov.py web export --repo <target-project> --json
```

Use this exact command surface. There is no `viewer` command group,
`--project-root` option, or guaranteed global `taskgov` executable.

The default file is
`state/projects/<project-id>/viewer/task-viewer.html`. Use `--output` only for a
fully approved destination. Snapshot v3 shows typed completion evidence and
bounded structured review evidence. It remains stale until regenerated and has
no server, live refresh, browser-side SQLite, edit controls, or automatic
browser launch.

## Operating Rules

- `db status`, `task list`, `task next`, `task current`, and `task show` must
  not create or migrate databases. Only `db init` creates or migrates one.
- `task add --status done` is prohibited. Initial `paused` is also prohibited.
- Pause only `in_progress` or `review_pending` work with a concise reason;
  resume explicitly to `in_progress`.
- `db status.counts.paused` is the exact population count. The
  `task current --status paused` result is bounded, while a
  `paused_tasks_present` warning on successful `task next` is advisory only
  and never changes ready candidates.
- Direct sequential transitions use the same predecessor rule as `task next`.
- Classify a new finding once: keep safely authorized acceptance work in the
  current task, record an unmet condition that prevents acceptance as its
  blocker, and durably `handoff record` everything else before continuing.
  Do not ask whether an Issue Skill exists or whether a pending handoff should
  stop otherwise accepted work.
- `handoff record` writes only a sanitized local `pending_handoff`. Exact
  replay returns the same row; a distinct recurrence requires an explicit
  stable `--occurrence-id`. Use `handoff list`/`show` to rediscover it.
  `db status.counts.handoff_pending` is the exact population count.
- A failed local handoff write returns `handoff_not_persisted`; stop only that
  execution unit until the same record is durable or the user explicitly
  accepts forgetting risk. A successful pending handoff never changes task
  selection, completion, events, or timestamps.
- Use `handoff withdraw` only on an explicit user request to withdraw or
  out-of-band-handle an undelivered record. This version has no Issue adapter,
  delivery, claim, or `handoff sync` command.
- Treat `done` as write-locked. Reopen it only with an isolated transition to
  `in_progress` and a concise `--reopen-reason`; every other write is rejected,
  and re-completion requires fresh gates.
- Lower a review tier only before the first review target, with a concise
  `--review-tier-change-reason`. A current-generation `changes_requested`
  receipt blocks completion until a newer target receives fresh qualifying
  receipts.
- Before completion, set the current review target and record the tier-required
  sanitized receipts/findings. Tier 2 normally requires two distinct
  independent PASS receipts for one target generation; a changed target needs
  fresh receipts.
- For review-before-commit Git work, stage only the intended files, set a
  `git_snapshot` target without `--revision`, obtain the required reviews, then
  create the completion commit through the project's Git workflow. The commit
  must have exactly the reviewed base as its one parent and the reviewed staged
  tree; root and merge commits are unsupported. A changed candidate requires a
  new target and fresh reviews. Unstaged and untracked files are excluded.
- Complete only after verification, review, and explicit `git_commit`,
  `external_revision`, or `commit_not_required` evidence pass. Unresolved high
  or medium findings block completion.
- Git resolution and done-time evidence revalidation, snapshot binding,
  evidence counting, and sequential checks are deterministic and do not add an
  LLM judgment.
- Writing commands affect only taskgov SQLite state: skill-local by default, or
  the explicitly selected `--db` path. `web export` writes one HTML snapshot
  only after explicit user intent.
- Do not modify target source, Git state, issues, PRs, or external services
  merely because this skill inspected task state.
- Reject secrets, raw logs, stack traces, environment dumps, large raw diffs,
  private prompts/reasoning, review transcripts, and full chat logs.

## Workflow References

Read [references/task_workflow.md](references/task_workflow.md) when choosing,
pausing, resuming, reviewing, or completing work.

Read [references/cli_contracts.md](references/cli_contracts.md) for exact
arguments, JSON shapes, errors, and examples.

This version does not record verification receipts, detect stale work, persist
session handoff checkpoints, page event or handoff history, create
child/checklist tasks, deliver records to an Issue Skill, or create
commits, branches, PRs, issue comments, network services, browser edit controls,
or unapproved target-project mutations.
