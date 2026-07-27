---
name: task-governance-tool
description: Project-scoped local-first task execution for Codex using the bundled taskgov CLI and canonical project-local state. Use when setting up or diagnosing task tracking, registering explicit tasks, rediscovering current or held work, selecting next actionable work, preserving explicit scope and acceptance, recording optional continuation checkpoints, handing off out-of-scope discoveries locally, or completing work through deterministic review and evidence gates.
---

# Task Governance Tool

Use this skill to continue governed work from compact local state. Treat the
target project's `AGENTS.md`, specifications, design, tests, and current user
decisions as authority. Treat taskgov state only as an execution aid.

## Scope And Invocation

Use one physical copy at
`.agents/skills/task-governance-tool` inside the governed project. Stateful use
from a user-wide copy, symbolic link, or Windows junction is unsupported.
Require Windows and Python 3.12 or later.

From the target-project root, invoke:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py <command> --json
```

When launching from inside the Skill directory, add
`--repo <target-project>` explicitly to every command. Otherwise the current
directory is the governed project; taskgov does not search for a Git root, and
a non-Git directory is valid.

Use only these 20 public command leaves:

- `setup`, `doctor`
- task `add`, `list`, `next`, `current`, `effort`, `show`, `edit`, `complete`,
  `checkpoint`
- handoff `record`, `list`, `show`, `withdraw`
- review `prepare`, target `set`, receipt `add`, finding `add`, finding
  `resolve`

Do not invent aliases, alternate state locations, maintenance commands, or
admin operations.

## First Use And Diagnosis

After physical project-scoped installation and ignore protection are in place,
run `setup` once when the user intends to use taskgov:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py setup --json
```

`setup` is explicit, noninteractive, and idempotent. It is the only command
that initializes or migrates project-local state, opts into local continuity
maintenance, and repairs the canonical offline projection. If the canonical
DB is missing but a valid managed generation remains, the same explicit call
recovers the newest valid generation before continuing normal migration and
repair. Do not invent a separate recovery command or path choice. The normal
Skill flow supplies no maintenance-policy options.

Run `doctor` only for an explicit diagnosis or install/release validation:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py doctor --json
```

`doctor` is the sole diagnostic. It is inherently read-only, performs no setup
or repair, and is never a prerequisite for setup or normal task work. Keep
recognized advisory and maintenance results on their fixed
`suggested_action=continue`; do not turn them into a question or routine stop.

## Deterministic Task Loop

Use this normal flow:

1. Rediscover with `task current --compact --json`.
2. If it returns an `in_progress` or `review_pending` row, resume the first
   such row in returned order. Otherwise select with
   `task next --compact --json`; returned `paused` and `blocked` rows remain
   recalled but do not suppress unrelated ready work.
3. Always read the resumed or selected task with
   `task show <task-id> --json`.
4. If selecting ready work, start it with
   `task edit <task-id> --status in_progress --json`.
5. Execute and verify against current authority. Record out-of-scope
   discoveries with `handoff record`; use `task checkpoint` only at a genuine
   continuation boundary.
6. Only when `task show.data.effort_advisory_enabled` is `true`, run one
   `task effort <task-id> --read-only --json` at the verification/review
   boundary. This is a mechanical route, not an LLM choice.
7. Set the exact review target, run `review prepare` once, obtain the required
   reviews, and record their receipts and findings.
8. Complete through `task complete` after verification and review gates pass.

For a no-finding Tier 2 task that must select new work, this graph uses at most
nine governance subprocess calls with the advisory disabled and ten when an
existing valid profile enables it. `doctor`, completion `--check`, and
`task checkpoint` are absent from the default success path. M14 adds no
mandatory question, judgment, or user-return stop.

## Operating Rules

- Register only explicit tasks. `task add --status done` and initial `paused`
  are prohibited.
- Rediscover `in_progress`, `review_pending`, `paused`, and `blocked` work with
  `task current`. Treat `paused_tasks_present` from `task next` as an advisory
  recall hint; inspect with `task current --status paused` without changing
  ready candidates.
- Enforce sequential predecessors for both selection and direct transitions.
  A blocked lane does not stop unrelated ready work.
- Copy a Task Contract only when scope and acceptance already exist in current
  authority. Leave revision zero otherwise without asking. Revise a Contract
  only from later explicit authority and record the reason.
- Pause only active/review-pending work with `--pause-reason`; block with
  `--blocked-reason`; resume explicitly to `in_progress`.
- Classify a new finding once: keep authorized acceptance work in the current
  task, record an unmet acceptance condition as its blocker, and immediately
  `handoff record` everything else before continuing.
- Use the same handoff command regardless of Issue tooling. A durable
  `pending_handoff` neither expands acceptance nor blocks otherwise accepted
  work. Use `handoff withdraw` only on explicit user direction.
- If privacy validation rejects handoff input, never repeat, quote, log, store,
  or forward the rejected raw content. Make at most one fresh attempt using a
  concise sanitized abstraction.
- If a local handoff cannot be persisted, stop only that execution unit until
  the same record is durable or the user explicitly accepts forgetting risk.
- Treat `done` as write-locked. Reopen only with an isolated transition to
  `in_progress` and `--reopen-reason`; fresh verification, target, and review
  evidence are then required.

## Review And Completion

Set a review target only after the exact material is ready. For review before a
Git completion commit, stage exactly the intended files and set
`--kind git_snapshot` without a revision. Unstaged and untracked files are
excluded. Use the single bounded `review prepare` packet for the independent
reviewers.

Tier 2 normally requires two distinct independent PASS receipts for the same
target generation. A changed target requires fresh receipts. A
current-generation `changes_requested` receipt or unresolved high/medium
finding blocks completion.

Complete with exactly one evidence form: `git_commit`, `external_revision`, or
`commit_not_required`. Git resolution, snapshot binding, receipt counting,
finding state, sequential ordering, and done-time revalidation are
deterministic. taskgov records evidence but does not stage, commit, branch,
push, open PRs, create Issues, or mutate target files.

## Safety And Privacy

- Keep secrets, tokens, authorization data, raw output, stack traces,
  environment dumps, private prompts/reasoning, full chat or review
  transcripts, and large diffs out of taskgov inputs.
- Do not modify target source, Git state, Issues, PRs, or external services
  merely because this skill inspected or updated local task state.
- Do not add network use, project-specific test strategy, Issue lifecycle,
  generic workflow automation, or hidden acceptance conditions.
- Leave local continuity artifacts to `setup` and bounded same-process
  post-commit maintenance. They add no LLM command choice or background
  process.

## References

Read [references/task_workflow.md](references/task_workflow.md) when selecting,
pausing, resuming, handing off, reviewing, checkpointing, or completing work.

Read [references/cli_contracts.md](references/cli_contracts.md) when exact
arguments, JSON fields, bounds, or errors matter.
