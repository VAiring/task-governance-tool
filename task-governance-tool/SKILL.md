---
name: task-governance-tool
description: Project-scoped local-first task execution for Codex using the bundled taskgov CLI and canonical project-local state. Use when setting up or diagnosing task tracking, registering explicit tasks, rediscovering current or held work, selecting next actionable work, preserving explicit scope and acceptance, recording bounded verification attestations or optional continuation checkpoints, handing off out-of-scope discoveries locally, or completing work through deterministic review and evidence gates.
---

# Task Governance Tool

Use taskgov state as an execution aid, not authority. The target project's
`AGENTS.md`, specifications, design, tests, and current user decisions govern
the work. Registration or inspection grants no implementation, Git, network,
or external-operation permission.

## Scope And Invocation

Use one physical project-scoped copy at
`.agents/skills/task-governance-tool`. User-wide, symbolic-link, and Windows
junction stateful use is unsupported. Require Python 3.12 or later on Windows,
Linux, or macOS. From the **target-project root**, examples use:

```powershell
python .agents/skills/task-governance-tool/scripts/taskgov.py <command> --json
```

On Linux/macOS use `python3` with the same path and arguments. If running from
inside the installed Skill directory instead, use `python scripts/taskgov.py`
and explicitly pass `--repo <target-project>` to every call. Otherwise the
current directory is the governed project; an enclosing Git worktree does not
change it, and a non-Git project is valid.

Use only the [public commands and common options](references/cli_contracts.md#invocation-and-public-inventory).
Do not invent aliases, alternate state paths, or administrative commands.

Reference reads need read access; ordinary state updates also need write access
under the installed package's canonical `state/`. When a host restriction is
known or access is denied, use [execution access guidance](references/cli_contracts.md#execution-access).
This adds no routine probe, doctor, or approval request when access is already valid.

## Start Or Resume

Read a linked section directly instead of searching line numbers or loading a
whole reference. From the target-project root:

```powershell
python .agents/skills/task-governance-tool/scripts/read_reference.py "references/task_workflow.md#bounded-operating-loop"
```

Use the existing link's package-relative `file#section` for other operations;
links inside the returned text resolve relative to its printed source file.
The reader returns the complete section, its subsections and ancestor
introductions. Follow applicable linked requirements; it does not infer which
conditional operations apply or recursively load them. Missing/invalid links
fail without a whole-file fallback. This replaces a document read, not a
taskgov call or additional prerequisite. Direct file reading remains valid.

Read the [bounded operating loop](references/task_workflow.md#bounded-operating-loop)
for ordinary work. It owns the normal sequence from `task context` through
verification and review gates to completion. `task context` takes **no Task ID**;
use `data.selected.task.task_id` from its successful response for later
`task edit`, `task show`, and other Task-addressed calls. Successful
`selection=none` means no actionable work; `ok=false` is a failed read, not
permission to guess another Task.

The context already contains the full Task Contract, checkpoint, and current
gates. Do not add current/next/show reads to reconstruct it. `task current`
remains available for explicit held-work inspection. `paused_tasks_present`
is an advisory, not another normal read.

Read only the linked responsibility needed for the operation or returned
condition, including its applicable exceptions and input rules. References
are not whole-file prerequisites. No read log, limit, new question, or extra
confirmation is required.

| Existing operation or condition | Read when applicable |
|---|---|
| First use, upgrade, or setup/migration required | [Setup and diagnosis](references/task_workflow.md#first-use-and-optional-diagnosis) |
| `project_relocation_required` | [Relocation preview and approval](references/cli_contracts.md#setup) |
| Explicit diagnosis or state/package error | [Doctor](references/cli_contracts.md#doctor) |
| Explicit taskization or active-Task scope addition | [Completion-based Task boundaries and registration](references/task_workflow.md#taskize-or-add-scope) |
| Copy or revise an authorized Contract | [Task Contract](references/task_workflow.md#task-contract) |
| `effort_advisory_enabled=true` | [Optional Effort Advisory](references/task_workflow.md#optional-effort-advisory) |
| A useful continuation boundary | [Optional checkpoint](references/task_workflow.md#optional-continuation-checkpoint) |
| Pause, block, or resume held work | [State transitions](references/task_workflow.md#pause-resume-and-block) |
| A discovery outside accepted scope | [Local handoff](references/task_workflow.md#scope-control-and-local-handoff) |
| Exact review material ready, including `git_snapshot` before commit | [Review and completion](references/task_workflow.md#review-and-completion) |
| Explicit Receipt/provenance or saved-history investigation | [Task audit](references/cli_contracts.md#task-audit-detail) |
| Explicit trusted-local Runner Plan authoring | [Plan actions](references/cli_contracts.md#runner-plan-actions) and [OS limits/example](references/cli_contracts.md#runner-plan-example-and-os-limits) |
| Maintenance warning after a successful write | [Continuity warnings](references/cli_contracts.md#internal-continuity-boundary) |

For exact options, fields, bounds, and errors, use the matching command in the
[CLI contents](references/cli_contracts.md#contents), not unrelated commands.
Verification without explicit Runner opt-in remains manual.

## Keep Scope And Evidence Honest

- Invoke task decomposition only for an explicit request to register or taskize
  already-authorized work, or an explicit scope addition to an active Task.
  Discovery, test failure, Effort, size, or model preference does not trigger it.
  Register only explicit work; `task add --status done` and initial paused are
  prohibited. Missing Contract detail alone creates no question.
- A failed verification or blocking review prevents completion of its Task;
  it does not by itself stop safe authorized diagnosis, repair, or unrelated
  ready work. Never weaken a test merely to obtain PASS. Change a wrong test
  only when current authority establishes the expected behavior; changing a
  Task Contract or acceptance requires later explicit authority.
- Classify discoveries once using the linked handoff rule. A durable
  `pending_handoff` does not expand acceptance or block otherwise accepted work.
  Use `handoff record` regardless of Issue tooling; withdraw only on explicit
  user direction.
- Tier 2 normally needs two distinct independent PASS reviews for the exact
  current target. Older evidence is audit-only; unresolved high/medium Findings
  still block. Record actual results and provenance, never inferred review work
  or resolutions derived from PASS. `done` is write-locked; use the
  [isolated reopen procedure](references/task_workflow.md#reopen) for approved
  follow-up work.

Read [references/reconciliation.md](references/reconciliation.md) only when
Effort returns `data.suggested_action=reconcile_scope`, or when a test or review
failure recurs after an attempted repair. One Effort result is one non-blocking
episode, not one per exceeded metric. Neither trigger adds a green-path command,
question, or stop.

## Safety And Privacy

Keep secrets, tokens, authorization data, raw output, stack traces, environment
dumps, private prompts/reasoning, full chat/review transcripts, and large diffs
out of taskgov inputs. If handoff input is privacy-rejected, never repeat,
quote, log, store, or forward the rejected raw content; make at most one fresh
attempt with a concise sanitized abstraction. See [input errors and privacy](references/cli_contracts.md#errors-and-privacy).

Taskgov does not stage files, create commits or branches, push, open PRs,
create Issues, or authorize target/external mutation. Leave canonical offline
projections and backup to explicit setup and bounded same-process maintenance;
they add no LLM command choice or background process. No network use, hidden
acceptance conditions, or project-specific test strategy is added by this Skill.

## License

Original copyrightable package material owned by Omoronine is licensed under
the Apache License, Version 2.0 (`Apache-2.0`). See [LICENSE](LICENSE).
No `NOTICE` is shipped because no concrete attribution duty was identified.
