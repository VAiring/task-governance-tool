---
name: task-governance-tool
description: Project-scoped local-first task execution for Codex using the bundled taskgov CLI and canonical project-local state. Use when setting up or diagnosing task tracking, registering explicit tasks, rediscovering current or held work, selecting next actionable work, preserving explicit scope and acceptance, recording bounded verification attestations or optional continuation checkpoints, handing off out-of-scope discoveries locally, or completing work through deterministic review and evidence gates.
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
directory is the governed project; taskgov never re-roots it to an enclosing
Git worktree, and a non-Git directory is valid.

Use only these 21 public command leaves:

- `setup`, `doctor`
- task `add`, `list`, `next`, `current`, `effort`, `show`, `edit`, `complete`,
  `checkpoint`
- handoff `record`, `list`, `show`, `withdraw`
- review `prepare`, target `set`, receipt `add`, finding `add`, finding
  `resolve`
- verification receipt `add`

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
maintenance, and repairs the canonical offline projections. If the canonical
DB is missing but a valid managed generation remains, the same explicit call
recovers the newest valid generation before continuing normal migration and
repair. Do not invent a separate recovery command or path choice. The normal
Skill flow supplies no maintenance-policy options.

For a package upgrade, preserve project-local state and run explicit `setup`.
There is no downgrade or restore command. A release rollback is valid only
when one matched pre-migration package, database, and managed-artifact set is
restored together; never run an older runtime against a newer schema or treat
a Git checkout alone as state rollback.

Project identity is immutable and its filesystem binding is mutable. Normal
commands and `doctor` never rebind state. If a command reports
`project_relocation_required`, run `setup --read-only --json` once and present
the returned bounded `relocation_preview` and planned writes to the user.
Wait for explicit approval in the current conversation; only then submit the
exact unexpired token with
`setup --confirm-relocation <exact-token> --json`. Never infer move/copy/fork
semantics or auto-confirm a preview. An expired or stale token requires a fresh
preview and fresh user approval. This exceptional flow adds nothing to the
normal Task loop.

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
   `task show <task-id> --json`. Its bounded completion history is audit
   context only and never satisfies a current gate.
4. If selecting ready work, start it with
   `task edit <task-id> --status in_progress --json`.
5. Finish the exact material against current authority. Record out-of-scope
   discoveries with `handoff record`; use `task checkpoint` only at a genuine
   continuation boundary.
6. Only when `task show.data.effort_advisory_enabled` is `true`, run one
   `task effort <task-id> --read-only --json` at the verification/review
   boundary. This is a mechanical route, not an LLM choice.
7. Set the exact review target and retain its returned generation,
   `verification_route`, and `blocking_code`; this existing operation may take
   the explicitly opted-in trusted-local Runner route.
8. Route only on that same response. `not_required` and `runner_pass` proceed
   without a Receipt. Only `receipt_required` runs the Task's verification
   outside taskgov and attests the aggregate result with
   `verification receipt add
   <task-id> --result <pass|fail|timeout> --duration-ms <milliseconds>
   --scope-coverage <full|partial> --expected-target-generation <generation>
   --json`. `blocked` requires a non-null returned code and stops closed; any
   missing, mismatched, or unknown route/code pair also stops.
9. Run `review prepare` once, obtain the required reviews, and record their
   receipts and findings.
10. Complete through `task complete` after verification and review gates pass.

Read [references/reconciliation.md](references/reconciliation.md) only when
the Effort result returns `data.suggested_action=reconcile_scope`, or when a
test or review failure recurs after an attempted repair. Treat one Effort
result as one non-blocking episode, not one episode per exceeded metric.
Neither trigger adds a green-path command, question, or stop.

For a no-finding Tier 2 task that must select new work, the manual/fallback graph
uses at most ten governance subprocess calls with the advisory disabled and
eleven when an existing valid profile enables it. `doctor`, completion `--check`, and
`task checkpoint` are absent from the default success path. This flow adds no
mandatory question, judgment, or user-return stop. The qualifying Runner-pass
branch omits the Verification Receipt call and therefore remains bounded to nine
or ten calls respectively.

## Operating Rules

- Invoke task decomposition only for an explicit request to register or taskize
  already-authorized work, or for an explicit scope addition to an
  `in_progress` or `review_pending` Task. Route either event through the
  one-pass guidance in [references/task_workflow.md](references/task_workflow.md).
  Discovery, a test failure, an Effort result, task size, or model preference
  does not invoke that guidance or add a normal-loop call.
- Register only explicit tasks. `task add --status done` and initial `paused`
  are prohibited.
- Rediscover `in_progress`, `review_pending`, `paused`, and `blocked` work with
  `task current`. Treat `paused_tasks_present` from `task next` as an advisory
  recall hint; inspect with `task current --status paused` without changing
  ready candidates.
- Enforce sequential predecessors for both selection and direct transitions.
  A blocked lane does not stop unrelated ready work.
- A failed verification or current blocking review result prevents completion
  of the affected Task; it does not by itself stop safe authorized diagnosis,
  repair, or unrelated ready work. Never weaken a test merely to obtain PASS.
  Change a wrong test only when current authority establishes the expected
  behavior; changing a Task Contract or acceptance requires later explicit
  authority.
- Copy a Task Contract only when scope and acceptance already exist in current
  authority. Leave revision zero otherwise without asking. Revise a Contract
  only from later explicit authority and record the reason.
- Explicit public `task add` and `task edit --verification` input is limited to
  1,000 characters. Schema-v18-through-v22 stored/read paths preserve an
  existing valid value through 1,000 characters; metadata and lifecycle edits
  continue to treat untouched verification bytes as stored state rather than
  caller input.
- Schema v19 sealed Bundle v1; current schema v22 automatically seals Bundle v2
  with the closed verification-basis union: `caller_attestation` or
  `not_required` with null `runner_observation`, or `runner_observation` with
  the qualifying exact observation. Evidence index v2 can reference preserved
  v1 Bundles without rewriting their bytes or digests. Pre-v19 cycles remain
  `legacy_unknown`; this adds no public command or JSON field, Skill trigger,
  normal-loop call, Analyzer or Viewer Evidence surface, or network/model call.
- Pause only active/review-pending work with `--pause-reason`; block with
  `--blocked-reason`; resume explicitly to `in_progress`.
- Classify a new finding once. Keep it in the current Task only when it is
  within accepted scope and current authority permits the repair, including
  acceptance-required work and regressions introduced by that Task; a failing
  test alone establishes neither condition. Record an unmet acceptance
  condition as its blocker only after safe authorized work is exhausted, and
  immediately `handoff record` everything else before continuing.
- Use the same handoff command regardless of Issue tooling. A durable
  `pending_handoff` neither expands acceptance nor blocks otherwise accepted
  work. Use `handoff withdraw` only on explicit user direction.
- If privacy validation rejects handoff input, never repeat, quote, log, store,
  or forward the rejected raw content. Make at most one fresh attempt using a
  concise sanitized abstraction.
- If a local handoff cannot be persisted, stop only that execution unit until
  the same record is durable or the user explicitly accepts forgetting risk.
- Treat `done` as write-locked. Reopen only with an isolated transition to
  `in_progress` and `--reopen-reason`; saved completion cycles remain
  audit-only, while a fresh current verification basis, target, and review
  evidence are required.

## Review And Completion

Set a review target only after the exact material is ready, retain its returned
generation and closed route, and apply that response directly. Only
`verification_route=receipt_required`—which can occur for a nonempty marker-`0`
expectation or the exact-current closed no-launch `m21_fallback`—runs the governed verification
outside taskgov and records one aggregate attestation with `verification receipt
add` before preparing review. `not_required` and `runner_pass` proceed without
verification or a Receipt. `blocked` reports the existing gate code and cannot
be overridden by a Receipt. For the M21 branch,
taskgov does not run the external command or retain its body or output. It
derives the version-1 verification subject from the locked target's authority
snapshot and verification criterion; there is no caller label or replacement
subject input. A `fail`, `timeout`, or `partial` Receipt requires a fresh target
generation before another run can become current. A migrated capture-version-0
target is read-only lineage: set a fresh
target before adding a Verification Receipt, Review Receipt, Review Finding, or
completion evidence. `review prepare` and resolving an existing Finding remain
allowed; the old target is never upgraded in place. For review before a Git
completion commit, stage exactly the intended files and set
`--kind git_snapshot` without a revision. Unstaged and untracked files are
excluded. Use the single bounded `review prepare` packet for the independent
reviewers; follow its exact-target instruction rather than ambient Git or
worktree state. Have reviewers return verdicts and findings. Record those
sanitized results from the trusted parent or orchestrator. For
`independent` or `self_review_fallback`, `review receipt add` requires
`--reviewer-class`, `--model-state`, `--skill-state`, and
`--context-relation`; declared model/Skill identifiers are conditional, and
`--review-profile`, `--review-lens`, and `--review-method` are repeatable.
These provenance options are forbidden for `not_required`. Taskgov
deterministically evaluates qualifying PASS receipts and changes-requested
receipts only for the current review target and generation. Any unresolved high
or medium finding from any recorded generation of that Task continues to block
the gate. Distinct reviewer keys prove distinct stored strings only; they do
not prove distinct people, LLMs, machines, independent processes,
independence, or authenticated provenance.

Public Review Receipts distinguish native v1 provenance, migrated v0 absence,
and `not_required` null. The v1 record is a bounded caller attestation; v0 does
not infer unknown values, and neither form upgrades the Receipt's existing
assurance or proves reviewer identity, model/Skill execution, competence,
independence, diversity, quality, or truth.

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
  post-commit maintenance, ordered Evidence projection, Viewer, then backup.
  They add no LLM command choice or background process.

## References

Read [references/task_workflow.md](references/task_workflow.md) when taskizing
explicit work, handling an explicit active-Task scope addition, selecting,
pausing, resuming, handing off, reviewing, checkpointing, or completing work.

Read [references/cli_contracts.md](references/cli_contracts.md) when exact
arguments, JSON fields, bounds, or errors matter.

Read [references/reconciliation.md](references/reconciliation.md) only for the
conditional reconciliation or repeated-failure triggers defined above.

## License

Original copyrightable package material owned by Omoronine is licensed under
the Apache License, Version 2.0 (`Apache-2.0`). See [LICENSE](LICENSE).
No `NOTICE` is shipped because no concrete attribution duty was identified.
