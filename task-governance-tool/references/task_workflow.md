# Task Workflow

Use this reference when selecting, starting, pausing, handing off, reviewing,
checkpointing, or completing work with `task-governance-tool`.

## Contents

- [Source Of Truth And Install Boundary](#source-of-truth-and-install-boundary)
- [First Use And Optional Diagnosis](#first-use-and-optional-diagnosis)
- [Bounded Operating Loop](#bounded-operating-loop)
- [Selection And Execution Boundary](#selection-and-execution-boundary)
- [Task Contract](#task-contract)
- [Optional Effort Advisory](#optional-effort-advisory)
- [Optional Continuation Checkpoint](#optional-continuation-checkpoint)
- [Pause, Resume, And Block](#pause-resume-and-block)
- [Scope Control And Local Handoff](#scope-control-and-local-handoff)
- [Review And Completion](#review-and-completion)
- [Register Tasks](#register-tasks)
- [Safety Boundary](#safety-boundary)

## Source Of Truth And Install Boundary

Read the target project's applicable `AGENTS.md`, specifications, design,
tests, and current user instructions before changing code or documentation.
Those sources outrank local task state.

Use one physical project-scoped copy at:

```text
<target-project>/.agents/skills/task-governance-tool
```

User-wide, symbolic-link, and Windows junction layouts are unsupported for
stateful use. From the target-project root, invoke the bundled script through
the `.agents` path. When running from inside the Skill directory, pass
`--repo <target-project>` explicitly to every command. Otherwise taskgov uses
the current directory and never re-roots it to an enclosing Git worktree. A
non-Git governed directory is valid.

Project identity is immutable; the governed-directory binding is mutable.
Normal commands and `doctor` never change that binding. Explicit setup
mechanically publishes supported same-binding legacy state into the fixed
package-local layout.

If a command reports `project_relocation_required`, do not infer whether the
directory was moved, copied, or forked. Run `setup --read-only --json` once,
present its bounded `relocation_preview` and planned writes, and wait for
explicit current user approval. Only after that approval, submit the exact
returned token with:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py setup --confirm-relocation <exact-token> --json
```

Never auto-confirm a preview. An expired or stale token requires a fresh
preview and fresh user approval. This exception is outside the bounded normal
Task loop and adds no routine command or question.

## First Use And Optional Diagnosis

After installation and ignore protection are ready, perform the one explicit
first-use operation when the user intends to use taskgov:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py setup --json
```

`setup` is the sole initializer and migrator. It also performs the one-way
local-continuity opt-in and repairs the canonical offline projection. It is
noninteractive and idempotent. The normal Skill workflow supplies no
maintenance-policy choice.

For a package upgrade, preserve project-local state and run explicit setup.
There is no downgrade or restore command. A release rollback restores one
matched pre-migration package, database, and managed-artifact set together;
never run an older runtime against a newer schema, mix generations, or treat a
Git checkout alone as state rollback.

When the fixed canonical DB is missing, setup first checks fixed-layout
managed generations. If no fixed source exists, it may select exactly one
eligible legacy source. A same-binding legacy primary or legacy backup-only
source is staged into the fixed layout; backup-only recovery runs
`database_restore` inside that private stage before `legacy_state_publish` and
never recreates the old legacy primary. A moved legacy backup-only source is
not eligible for relocation and fails no-write as
`project_state_unreadable`. If recognized fixed-layout managed recovery
material exists but none is valid, setup fails without creating empty state.
Do not ask the model to select a generation, path, or recovery command.

Use `doctor` only when the user asks for diagnosis or install/release
validation, or when a command returns a setup, migration, package, layout, or
state-readiness error:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py doctor --json
```

Doctor is inherently read-only. It never initializes, migrates, repairs,
backs up, renders, runs project tests, or changes the target project. For a
Git-candidate target, only its single bounded effective-ignore preflight may
inspect Git. It is not a prerequisite for setup or normal task work. Recognized
package advisories and readable maintenance outcomes retain
`suggested_action=continue`; do not convert them into a question, handoff,
pause, blocker, or routine stop.

## Bounded Operating Loop

Use this deterministic graph for a normal no-finding Tier 2 task:

1. Rediscover current work:

   ```powershell
   python .agents/skills/task-governance-tool/scripts/taskgov.py task current --compact --json
   ```

2. If `task current` contains an `in_progress` or `review_pending` row, resume
   the first such row in returned order. Otherwise select ready work; returned
   `paused` and `blocked` rows stay rediscovered but do not suppress unrelated
   candidates:

   ```powershell
   python .agents/skills/task-governance-tool/scripts/taskgov.py task next --compact --json
   ```

3. Always inspect the resumed or selected task:

   ```powershell
   python .agents/skills/task-governance-tool/scripts/taskgov.py task show <task-id> --json
   ```

   The same read returns bounded completion history. Treat saved cycles only
   as audit context; they never satisfy the current verification, review, or
   completion gate.

4. When a ready task was selected, start it:

   ```powershell
   python .agents/skills/task-governance-tool/scripts/taskgov.py task edit <task-id> --status in_progress --json
   ```

5. Execute and verify the task. Record real progress with bounded notes only
   when useful. Record out-of-scope discoveries immediately with
   `handoff record`.
6. If and only if the mandatory `task show` result has
   `data.effort_advisory_enabled=true`, make one `task effort` observation at
   the verification/review boundary.
7. Set the exact review target, prepare one bounded review packet, record the
   required review receipts/findings, and complete the task.

When step 2 is needed, this is at most nine governance subprocess calls with
the Effort Advisory disabled and ten when an existing valid profile enables
it. The conditional branch is a boolean route from `task show`, not an LLM
choice. The count includes two actual Tier 2 receipt writes; it excludes the
two independent review model decisions and real progress notes.

`doctor`, `task complete --check`, and `task checkpoint` are optional and
absent from the default success path. Do not add them mechanically to every
task.

## Selection And Execution Boundary

- Treat `optional` tasks as actionable when `status=ready`.
- Treat `sequential` tasks as actionable only when they are ready and every
  earlier task in the same lane is `done` or `cancelled`.
- Keep unrelated optional tasks and other lanes actionable when one lane
  blocks.
- Preserve deterministic priority/lane/order selection from the CLI; do not
  re-rank candidates semantically inside the Skill.
- Treat `paused_tasks_present` from `task next` as an advisory recall hint.
  Inspect the bounded paused subset without changing returned candidates:

  ```powershell
  python .agents/skills/task-governance-tool/scripts/taskgov.py task current --repo <target-project> --status paused --json
  ```

Before starting each execution unit, state its intended outcome, write scope,
verification gate, and review tier. Update the task only after those values
come from current authority.

## Task Contract

Use a Contract only when scope and acceptance are already explicit in the
current user request, an approved roadmap, or task-registration input. Copy
those values deterministically. Never ask for missing Contract fields and
never infer that duration, effort, or risk makes a Contract mandatory.

Record revision 1 during registration:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py task add --repo <target-project> --title "Bounded change" --contract-scope "Authorized files and behavior" --contract-acceptance "Exact completion condition" --contract-constraints "No unrelated cleanup" --contract-authority-ref "roadmap:TG-M14.7" --json
```

Alternatively, activate revision 1 only on an exact revision-zero
`ready|blocked -> in_progress` transition:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py task edit --repo <target-project> <task-id> --status in_progress --contract-scope "Authorized files and behavior" --contract-acceptance "Exact completion condition" --json
```

Make a later semantic revision only from later explicit authority and include
its reason:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py task edit --repo <target-project> <task-id> --contract-scope "Revised explicit scope" --contract-acceptance "Revised explicit acceptance" --contract-authority-ref "user_instruction:<task-id>:<revision>" --contract-change-reason "User changed the accepted boundary" --json
```

Do not use a document produced by the current task to authorize that task's own
expansion. Hand off proposed hardening outside the current Contract. A
canonically unchanged Contract is a write-free replay. A semantic revision
invalidates current completion/review eligibility so fresh gates apply without
another scope question.

## Optional Effort Advisory

The default JSON `task show` result always supplies
`effort_advisory_enabled=true|false`. Only `true` mechanically adds:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py task effort --repo <target-project> <task-id> --read-only --json
```

Run it once at the existing verification/review boundary. Continue directly
when `suggested_action=continue`. When
`suggested_action=reconcile_scope`, read
[reconciliation.md](reconciliation.md) and run one non-blocking
session-local episode for the whole result, not one episode per exceeded
metric. Neither action by itself asks the user, creates a handoff, expands
acceptance, pauses, blocks, fails, or adds a completion/review gate. A separate
concrete safety problem still follows the project's existing safety rules.

Do not create or change an Effort Advisory profile without explicit
project/user authority. Repeated observations add no value and require no
acknowledgement.

## Optional Continuation Checkpoint

Record a checkpoint only at a genuine continuation boundary when a compact
structured resume note is useful:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py task checkpoint --repo <target-project> <task-id> --summary "Completed bounded implementation slice" --next-action "Run focused verification" --unresolved-risk "Review exact staged revision" --json
```

Keep summary and next action within 1,024 UTF-8 bytes each. Supply
`--unresolved-risk` at most eight times, at most 512 bytes each and 4,096 bytes
combined; the caller payload is capped at 6,144 bytes. Exact replay of the
latest checkpoint for the same Contract revision writes nothing.

A checkpoint is optional. Never require it for pause, resume, review, or
completion. It does not change task status, selection, gates, or
`tasks.updated_at`. Default `task current` and `task show` expose only the
latest checkpoint; compact selection intentionally omits its content.

## Pause, Resume, And Block

Pause only `in_progress` or `review_pending` work with a concise reason:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py task edit --repo <target-project> <task-id> --status paused --pause-reason "Waiting for a safe continuation window" --json
```

Rediscover it with `task current --status paused`, then resume explicitly:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py task edit --repo <target-project> <task-id> --status in_progress --json
```

Record a blocking condition on its owning task:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py task edit --repo <target-project> <task-id> --status blocked --blocked-reason "Waiting for user decision on the accepted boundary" --json
```

After blocking one lane, return to `task next` for unrelated ready work.

## Scope Control And Local Handoff

Classify each discovery once:

1. Keep it in the current Task only when it is within accepted scope and
   current authority safely permits the repair. This includes
   acceptance-required work and regressions introduced by that Task; a failing
   test alone establishes neither condition.
2. Record it as the current Task's blocker only when it prevents acceptance
   and safe authorized work for the affected Task or lane is exhausted.
3. Otherwise, durably hand it off before continuing:

   ```powershell
   python .agents/skills/task-governance-tool/scripts/taskgov.py handoff record --repo <target-project> <task-id> --summary "Concise out-of-scope discovery" --rationale "Outside current acceptance" --json
   ```

Always use `handoff record`, regardless of whether Issue tooling is absent or
may be added later. This release stores `pending_handoff` locally and performs
no delivery, claim, semantic triage, prioritization, or Issue lifecycle work.
A successful handoff never expands acceptance or changes task selection,
completion, events, or timestamps.

Exact canonical replay returns the existing record. Supply
`--occurrence-id <stable-id>` only when a user or deterministic source already
provides a stable identity for a genuinely distinct occurrence.

Rediscover records with:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py handoff list --repo <target-project> --json
python .agents/skills/task-governance-tool/scripts/taskgov.py handoff show --repo <target-project> <handoff-id> --json
```

Use `handoff withdraw` only when the user explicitly withdraws or handles an
undelivered record out of band:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py handoff withdraw --repo <target-project> <handoff-id> --reason "Explicit user direction" --json
```

If a record returns `privacy_rejected`, never repeat, quote, log, store, or
forward the rejected raw input. Make at most one new attempt with a newly
written concise sanitized abstraction. If local persistence returns
`handoff_not_persisted`, stop only that execution unit until the same record is
durable or the user explicitly accepts forgetting risk.

## Review And Completion

Move implementation-complete work to `review_pending` when its required review
gate is still open:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py task edit --repo <target-project> <task-id> --status review_pending --json
```

Set the exact target. For review-before-commit Git work, stage only intended
files and capture the stage-0 index without a caller revision:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py review target set --repo <target-project> <task-id> --kind git_snapshot --json
```

Unstaged and untracked files are outside that target. For already committed or
non-Git material, use `git_commit`, `diff_fingerprint`, or
`external_revision` with `--revision`.

Prepare one read-only packet after the target is set:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py review prepare --repo <target-project> <task-id> --read-only --json
```

Use that one bounded packet for the reviewers. Do not reconstruct separate
task, Contract, target, and changed-path prompts. The command launches no
reviewer and imports or stores no result.

Follow the packet's target-kind instruction exactly:

- for `git_snapshot`, inspect only the matching stage-0 index against the
  stored base using the cached diff and index blobs; never count unstaged or
  untracked worktree content;
- for `git_commit`, inspect the target commit's tree and blobs against its first
  parent, or the empty tree for a root, rather than ambient `HEAD` or worktree;
- for `diff_fingerprint`, return no PASS until exact review material and its
  binding to the fingerprint are available; and
- for `external_revision`, return no PASS until exact externally supplied
  material is bound to that revision.

The independent reviewer returns the actual verdict and findings. The trusted
parent/orchestrator that requested the review records concise sanitized
receipts and findings as attestations of those results. Taskgov
deterministically evaluates qualifying PASS receipts and changes-requested
receipts only for the current review target and generation. Any unresolved high
or medium finding from any recorded generation of that Task continues to block
the gate. Distinct reviewer keys prove distinct stored strings only; they do
not prove distinct people, LLMs, machines, independent processes,
independence, or authenticated provenance.

Tier 2 normally requires two distinct independent PASS receipts for one target
generation:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py review receipt add --repo <target-project> <task-id> --reviewer <reviewer-a> --kind independent --verdict pass --summary "No blocking findings" --json
python .agents/skills/task-governance-tool/scripts/taskgov.py review receipt add --repo <target-project> <task-id> --reviewer <reviewer-b> --kind independent --verdict pass --summary "No blocking findings" --json
```

Record findings with `review finding add` and resolve them with
`review finding resolve`. A current-generation `changes_requested` receipt or
an unresolved high/medium finding blocks completion. After a meaningful fix,
set a newer target and obtain a fresh current-generation review result. A
result that remains blocking counts as an unsuccessful remediation cycle;
completion still requires fresh qualifying PASS receipts.
If test or review failure recurs after an attempted repair, read
[reconciliation.md](reconciliation.md) before another materially equivalent
repair or remediation cycle.

Optionally preview completion readiness without writing:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py task complete --repo <target-project> <task-id> --verification-complete --review-complete --completion-evidence-kind git_commit --completion-revision <hash> --check --read-only --json
```

The check is not an authorization token; the write revalidates the same gates.
It is not part of the default success path.

Complete with exactly one evidence form:

```powershell
# Git commit
python .agents/skills/task-governance-tool/scripts/taskgov.py task complete --repo <target-project> <task-id> --verification-complete --review-complete --completion-evidence-kind git_commit --completion-revision <hash> --json

# Explicitly approved external durable revision
python .agents/skills/task-governance-tool/scripts/taskgov.py task complete --repo <target-project> <task-id> --verification-complete --review-complete --completion-evidence-kind external_revision --completion-revision <revision> --completion-evidence-reason "Approved external release" --external-revision-approved --json

# No managed material changed; requires a matching diff_fingerprint target
python .agents/skills/task-governance-tool/scripts/taskgov.py task complete --repo <target-project> <task-id> --verification-complete --review-complete --commit-not-required --json
```

For a `git_snapshot` target, create the completion commit through the
project's approved Git workflow after both reviews, without changing the
reviewed staged tree. Complete with that commit. The deterministic binding
requires exactly one parent equal to the captured base and an identical tree;
root and merge commits are unsupported.

taskgov validates and records evidence only. It does not stage files, create
commits or branches, push, open PRs, or write Issue comments.

Treat `done` as write-locked. Reopen only as an isolated operation:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py task edit --repo <target-project> <task-id> --status in_progress --reopen-reason "Approved follow-up correction" --json
```

Do not combine reopen with another edit. Reopening preserves historical events
and saved completion cycles, but those records remain audit-only and the Task
requires fresh verification, target, receipts, and completion evidence.

## Register Tasks

Register only explicit user-approved work:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py task add --repo <target-project> --title "Implement bounded change" --kind sequential --lane TG-M14 --order 20 --priority high --review-tier 2 --json
```

Do not invent dependency graphs or import a project plan. Initial `done` and
initial `paused` are rejected. For an initially blocked task, supply
`--blocked-reason`. Let the deterministic CLI fill omitted sequential
lane/order fields and return them.

## Safety Boundary

- Keep secrets, tokens, authorization data, raw stdout/stderr, stack traces,
  environment dumps, private prompts/reasoning, full chats/reviews, and large
  diffs out of local task inputs.
- Use concise summaries, reasons, notes, checkpoints, findings, and receipts.
- Do not let inspection authorize target-file, Git, Issue, PR, network, or
  external-service mutation.
- Leave backup and offline-projection maintenance to setup and bounded
  same-process post-commit processing. Do not make an LLM choose, schedule, or
  monitor it.
