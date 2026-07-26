---
name: task-governance-tool
description: Project-scoped local-first task tracking for Codex using the bundled taskgov CLI and skill-local SQLite state. Use when planning or registering explicit tasks, initializing or inspecting task state, rediscovering current work, selecting next actionable work, pausing or handling blockers, durably handing off out-of-scope discoveries, updating or completing tasks with structured review and completion evidence, or creating or regenerating a user-requested offline static Task Viewer.
---

# Task Governance Tool

Use this skill to work from compact local task state instead of loading large
task-status documents. Treat the target project's `AGENTS.md`, specs, design
docs, tests, and user decisions as the source of truth; SQLite is only a local
task-state helper.

Use one physical copy per governed project at
`.agents/skills/task-governance-tool`. Stateful use from a user-wide copy,
symbolic link, or Windows junction is unsupported. The verified runtime is
Windows with Python 3.12 or later; Linux and macOS are unverified.

## Quick Start

From the target-project root, run the bundled CLI through its project-scoped
path:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py db status --json
python .agents/skills/task-governance-tool/scripts/taskgov.py task current --json
python .agents/skills/task-governance-tool/scripts/taskgov.py task current --status paused --json
python .agents/skills/task-governance-tool/scripts/taskgov.py task next --json
python .agents/skills/task-governance-tool/scripts/taskgov.py task show <task-id> --json
python .agents/skills/task-governance-tool/scripts/taskgov.py handoff list --json
```

If the current directory is the installed Skill folder, pass
`--repo <target-project>` explicitly. Without `--repo`, the current directory
is the governed directory; no Git-root search occurs, and non-Git directories
are valid.

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
`state/projects/<project-id>/taskgov.sqlite` under this installed skill.
Canonical absolute-path identity changes when a project moves; do not infer or
rewrite relocation.

## Static Task Viewer

Create or regenerate the offline viewer only when the current user explicitly
asks for that HTML artifact. Inspection alone authorizes CLI reads, not an HTML
write.

Preview without writing:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py web export --read-only --json
```

After explicit user intent, generate the default snapshot:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py web export --json
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

- For an explicit installed-package or release-copy inspection, run
  `self status --read-only --json`. Follow its fixed
  `suggested_action=continue`; `modified` or `unknown` must not create a
  question, handoff, Issue/PR action, update, repair, GitHub call, or task stop.
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
- Copy an optional Task Contract only when explicit scope and acceptance
  already exist in current authority. Otherwise keep revision zero without a
  question. Later semantic revisions require explicit later authority and a
  reason; canonically unchanged input is a write-free replay.
- If `db status` reports an explicitly enabled Effort Advisory, run
  `task effort <task-id> --read-only --json` once at the existing
  verification/review boundary. Always follow its fixed `continue` action;
  never turn its warning or unknown attribution into a question, handoff,
  acceptance change, status change, or completion gate.
- Classify a new finding once: keep safely authorized acceptance work in the
  current task, record an unmet condition that prevents acceptance as its
  blocker, and durably `handoff record` everything else before continuing.
  Do not ask whether an Issue Skill exists or whether a pending handoff should
  stop otherwise accepted work.
- `handoff record` writes only a sanitized local `pending_handoff`. Exact
  replay returns the same row; a distinct recurrence requires an explicit
  stable `--occurrence-id`. Use `handoff list`/`show` to rediscover it.
  `db status.counts.handoff_pending` is the exact population count.
- If the privacy guard rejects handoff input, never repeat, quote, log, store,
  or forward that rejected raw content. Make at most one new attempt using a
  newly written concise sanitized abstraction. If it is rejected again, stop
  that recovery attempt and report only the fixed sanitized error.
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
generic risk profiles, fixture/retry/test-metric scanners, or unapproved
target-project mutations. Package self-status is not a signature, upstream
update check, downloader, installer, or repair tool.
