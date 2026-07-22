---
name: task-governance-tool
description: Project-scoped local-first task tracking for Codex using the bundled taskgov CLI and skill-local SQLite state. Use when planning or registering explicit tasks, initializing or inspecting task state, rediscovering current work, selecting next actionable work, pausing or handling blockers, updating or completing tasks with structured review and completion evidence, or creating or regenerating a user-requested offline static Task Viewer.
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
python scripts/taskgov.py task next --repo <target-project> --json
python scripts/taskgov.py task show --repo <target-project> <task-id> --json
```

First-use flow:

1. Run `db status` without creating files.
2. After project-scoped installation, verify that generated `state/` is ignored.
   If the user intends to use local tracking, run `db init`. Package creation
   itself never initializes a target database.
3. Register only explicit user-approved tasks with `task add`; do not import
   large task files or invent dependency graphs.
4. Use `task current` to rediscover started, paused, blocked, or review-pending
   work. Use `task next` only for new ready work, then inspect with `task show`.

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
- Direct sequential transitions use the same predecessor rule as `task next`.
- Before completion, set the current review target and record the tier-required
  sanitized receipts/findings. Tier 2 normally requires two distinct
  independent PASS receipts for one target generation; a changed target needs
  fresh receipts. Any current-generation `changes_requested` also requires a
  newer target and fresh review.
- Complete only after verification, review, and explicit `git_commit`,
  `external_revision`, or `commit_not_required` evidence pass. Unresolved high
  or medium findings block completion. Git/external completion must match the
  same current review target; a diff fingerprint cannot close revision-bearing
  evidence, and stored Git evidence is rechecked at completion.
- Treat `done` as terminal and immutable. All later task edits and structured
  review writes fail; register follow-up work as a new task. A tier downgrade
  also needs `--review-tier-change-reason`, an initialized review latch that
  has never started, and no review target. Entering review or accepting
  `--review-complete` irreversibly starts that latch. Legacy or ambiguous latch
  state fails closed.
- Git resolution, evidence counting, and sequential checks are deterministic
  and do not add an LLM judgment.
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
handoff checkpoints, page event history, create child/checklist tasks, or create
commits, branches, PRs, issue comments, network services, browser edit controls,
or unapproved target-project mutations.
