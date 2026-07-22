# Task Workflow

Use this reference when selecting, starting, blocking, or completing local
tasks with `task-governance-tool`.

## Contents

- [Source Of Truth](#source-of-truth)
- [Minimal Operating Loop](#minimal-operating-loop)
- [Inspect Ready Work](#inspect-ready-work)
- [Selection Semantics](#selection-semantics)
- [Execution Unit Boundary](#execution-unit-boundary)
- [Pause And Resume](#pause-and-resume)
- [Blockers](#blockers)
- [Completion And Review](#completion-and-review)
- [Create An Offline Task Viewer](#create-an-offline-task-viewer)
- [Register Tasks](#register-tasks)

## Source Of Truth

The target project's governing docs and the current user request outrank the
SQLite task database. Before code or documentation work, read the target
project's applicable `AGENTS.md`, specs, design docs, tests, and local rules.

The task database stores compact execution state only. Do not treat it as a
hidden authority for product decisions.

Use the project-scoped installed copy for the governed project, normally under
`.agents/skills/task-governance-tool`. If this skill is available only from a
user-wide or global install, ask the user before initializing state; normal
operation expects a separate installed copy per project.

## Minimal Operating Loop

Use this loop when the user asks to work from local task state:

1. Inspect: `db status`.
2. Initialize only if needed and explicitly intended: `db init`.
3. Register only explicit tasks: `task add`.
4. Rediscover started work: `task current`.
5. If no current task should resume, choose ready work: `task next`.
6. Inspect the chosen task: `task show`.
7. Update local state: `task edit`.

If a task blocks, mark that task `blocked` with a concise reason, then return to
`task next` for unrelated ready work.

## Inspect Ready Work

1. Check database state without mutation:

   ```powershell
   python scripts/taskgov.py db status --repo <target-project> --json
   ```

2. Ask for ready candidates:

   ```powershell
   python scripts/taskgov.py task next --repo <target-project> --limit 5 --json
   ```

3. Inspect the chosen task before acting:

   ```powershell
   python scripts/taskgov.py task show --repo <target-project> <task-id> --json
   ```

If the database is missing, initialize it only when the user intends to use this
local state store for the current project-scoped install:

```powershell
python scripts/taskgov.py db init --repo <target-project> --json
```

## Selection Semantics

- `optional` tasks are actionable when `status=ready`.
- `sequential` tasks are actionable when `status=ready` and all earlier tasks in
  the same `lane` are `done` or `cancelled`.
- A blocked or incomplete sequential lane blocks only later work in that lane.
- Ready optional tasks and ready tasks in other lanes remain selectable.
- Sorting is by priority rank (`urgent`, `high`, `normal`, `low`), then lane,
  lane order with nulls last, creation time, and task ID.

## Execution Unit Boundary

Before starting a task, declare:

- intended outcome
- write scope
- verification gate
- review tier or review gate

Then mark the task in progress when the user-approved workflow expects local
tracking:

```powershell
python scripts/taskgov.py task edit --repo <target-project> <task-id> --status in_progress --json
```

## Pause And Resume

Pause only work already in `in_progress` or `review_pending`, with a concise
sanitized reason:

```powershell
python scripts/taskgov.py task edit --repo <target-project> <task-id> --status paused --pause-reason "Waiting for a safe continuation window" --json
```

Use `task current` in a later session to rediscover it. Resume explicitly to
`in_progress`; the current pause reason is cleared while its bounded transition
event remains:

```powershell
python scripts/taskgov.py task edit --repo <target-project> <task-id> --status in_progress --json
```

Paused work is incomplete and blocks later work in the same sequential lane.

## Blockers

When a task cannot continue, record the blocker concisely:

```powershell
python scripts/taskgov.py task edit --repo <target-project> <task-id> --status blocked --blocked-reason "Waiting for user decision on ..." --json
```

After recording a blocker, ask for another ready task with `task next`. Do not
let one blocked lane stop unrelated optional work or work in other lanes.

## Completion And Review

Move a task to `review_pending` when implementation is done but the required
review gate is not complete:

```powershell
python scripts/taskgov.py task edit --repo <target-project> <task-id> --status review_pending --json
```

Mark a task `done` only after:

- the task's required verification has passed or has a documented
  user-approved exception
- the required review gate has passed, or a valid fallback/not-required review
  decision exists for the task's review tier
- there are no unresolved high or medium review findings
- the completion commit gate is satisfied

Set one review target before recording results. Use a Git commit when the
reviewed state is committed, or a deterministic diff fingerprint/external
revision when that is the actual target:

```powershell
python scripts/taskgov.py review target set --repo <target-project> <task-id> --kind git_commit --revision <hash> --json
```

Record only sanitized structured outcomes. A normal Tier 2 task needs two
distinct independent PASS receipts for the same target generation:

```powershell
python scripts/taskgov.py review receipt add --repo <target-project> <task-id> --reviewer <reviewer-a> --kind independent --verdict pass --summary "No blocking findings" --json
python scripts/taskgov.py review receipt add --repo <target-project> <task-id> --reviewer <reviewer-b> --kind independent --verdict pass --summary "No blocking findings" --json
```

This is two LLM review judgments in the normal Tier 2 path. If findings cause a
meaningful change, set the review target again and obtain two fresh judgments;
the new generation prevents older receipts from being reused. Git resolution,
receipt counting, finding-state checks, and sequential-order checks are
deterministic and add no LLM judgment. Record findings with `review finding add`
and resolve them with `review finding resolve`; an unresolved high or medium
finding blocks completion. A current-generation `changes_requested` receipt
also blocks completion even if enough PASS receipts exist; set a newer target
and obtain fresh review after the correction.

Final evidence must identify the reviewed revision. Git completion requires a
matching Git review target, and external completion requires the same external
revision target. A diff fingerprint cannot be deterministically bound to a
later Git commit/tree or external revision, so retarget that final revision and
review it before completion. `commit_not_required` requires a diff target as
the explicit no-managed-material-change review decision.

For changed Git-managed materials, first create the project commit through the
approved project workflow, then record it. `taskgov` verifies the revision
read-only and stores its canonical full commit ID; a unique short hash or
annotated tag is accepted only when Git resolves it unambiguously:

```powershell
python scripts/taskgov.py task edit --repo <target-project> <task-id> --status done --verification-complete --review-complete --completion-commit-hash <hash> --json
```

For an approved durable revision outside the target Git history, use the
explicit external evidence form. The reason and acknowledgement are required;
an arbitrary string passed to `--completion-commit-hash` is not an external
revision bypass:

```powershell
python scripts/taskgov.py task edit --repo <target-project> <task-id> --status done --verification-complete --review-complete --completion-evidence-kind external_revision --completion-revision <revision> --completion-evidence-reason "Approved external release" --external-revision-approved --json
```

If no managed materials changed, explicitly record that no completion commit is
required:

```powershell
python scripts/taskgov.py task edit --repo <target-project> <task-id> --status done --verification-complete --review-complete --commit-not-required --json
```

If valid typed evidence was already recorded, the later done transition still
requires `--verification-complete` and `--review-complete`. Historical
`legacy_unverified` evidence is retained for audit but cannot close any new
done transition. Stored Git evidence and Git review targets are resolved again
during the done transition; evidence that no longer exists fails closed.

Done is terminal and immutable. Every later `task edit` and structured review
write is rejected with `task_done_immutable`, including an attempted status
change. Register any follow-up work as a new task and leave the completed audit
record unchanged.

If a review tier must be lowered, provide a concise sanitized rationale while
the task is `ready`, in progress, paused, or blocked and review has never
started. A new task has one `task_added_review_unstarted` machine event; entry
into review-pending or any accepted `--review-complete` confirmation records
`review_started`. Exactly one initialization marker, no start marker, and an
empty target at generation `0` are required. Missing, duplicate, or legacy
marker state fails closed, and pause/resume never clears a start marker. Do not
combine the downgrade with completion evidence or gate confirmations.

```powershell
python scripts/taskgov.py task edit --repo <target-project> <task-id> --review-tier 1 --review-tier-change-reason "Scope reclassified after governing-rule review" --json
```

`taskgov` records the commit state but does not create commits, branches, PRs,
or issue comments. To trace changed materials for a completed Git task, inspect
the target project's history from the stored hash:

```powershell
git show --name-only <completion_commit_hash>
```

Use `--add-note` for concise local notes. Keep notes sanitized and short; do not
store raw command output, stack traces, secrets, large diffs, or private chat
logs.

## Create An Offline Task Viewer

Use `web export` writing mode only when the current user explicitly asks to
create or regenerate the viewer. For ordinary inspection or summarization, use
`task list`, `task next`, and `task show` without creating HTML.

Use the bundled command exactly as shown below. The CLI has no `viewer export`
or `--project-root` alias, and a global `taskgov` executable is not guaranteed.

Preview the path and snapshot counts without writing:

```powershell
python scripts/taskgov.py web export --repo <target-project> --read-only --json
```

Generate the default skill-local file after explicit user intent:

```powershell
python scripts/taskgov.py web export --repo <target-project> --json
```

The default output is
`state/projects/<project-id>/viewer/task-viewer.html` under the installed skill
folder. Pass `--output <html-path>` only after the user approves that complete
destination; an explicit parent must already exist. Opening the browser is a
separate user action.

Treat the page as a timestamped snapshot. Task changes do not appear until the
user explicitly requests regeneration. The page does not connect to SQLite,
start a server, refresh itself from the database, or provide task-edit controls.
Use the CLI to update task state, then regenerate only with current user intent.

## Register Tasks

Register only explicit user-approved tasks. The MVP does not import large task
files, create draft dependency graphs, run approval workflows, or register
persistent project profiles.

Do not use `task add` to close current work; initial `done` is rejected.
Register the task first, then use
`task edit --status done` with the required verification, review, and completion
commit flags when the task is actually complete.

```powershell
python scripts/taskgov.py task add --repo <target-project> --title "Implement task next" --kind sequential --lane TG-M3 --order 20 --priority high --review-tier 2 --json
```

For sequential tasks, omitted lane/order are auto-filled and returned in the
command output. For initially blocked tasks, `--blocked-reason` is required.
