---
name: task-governance-tool
description: Project-scoped, local-first task governance for Codex using taskgov. Use for setup/read-only diagnosis, explicit task planning and registration, current/next or held-work selection, scope/acceptance tracking, pause/block handling, local handoff, bounded Review Packets, verification receipts, optional checkpoints, and completion through deterministic review and evidence gates.
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

## Assigned Independent Review

When assigned an independent review with generated read/save instructions,
follow that self-contained request and its output; no additional Skill procedure
read is needed. Read Skill material when it is actually authority or part of the
reviewed target. The parent's Task loop below is not your workflow. For direct
complete-Packet transport, use the conditional
[direct Packet reviewer procedure](references/task_workflow.md#direct-packet-reviewer).
For an unclear role, use the [reviewer boundary](references/task_workflow.md#independent-reviewer).

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
for ordinary work, from selection through verification and review gates to completion.
`task context` takes **no Task ID**; the loop owns its response use, automatic
ownership and conditional recovery. Retain the selected context for later
operations.

Edit, completion, and target-set acknowledgements omit unchanged description,
verification, kind/lane/order, priority, tags and creation time. Retain them from
this context; all changed values are returned and no extra read is needed.

Before review preparation, [choose the completion route](references/task_workflow.md#choose-the-completion-route):
authorized supported snapshots use `prepare-finalization`; conditional manual
and direct transports remain available. When reviews need waiting and the
authorized service is enabled and available, use automatic waiting and end the
parent turn after confirmed readiness. The linked procedure retains waiting-free
continuation and the existing exceptions. Use the [review workflow](references/task_workflow.md#prepare-and-record-reviews)
for dispatch. Assigned independent reviewers use the separate entry above.

When a received integrated report's visible body matches the original Task/target
generation, shows `status=completed` with successful stages and actual Task done,
and supplies the reporting facts without unresolved contradiction or action,
report directly without another guide read, routine query or successful-operation
replay. Include Findings (also low/resolved), warnings, limitations and work
reported as `not_recorded`; their presence alone does not exclude this path.
Labels, review PASS or `reviews_ended` alone do not prove completion or unseen
content. For other results, including incomplete, unknown, failed, conflicting
or action-requiring reports, use [Continue after reviews](references/task_workflow.md#continue-after-reviews).
Starting the next Task still requires its normal authority reads and gates.

Read only the linked responsibility needed for the operation or returned
condition, including its applicable exceptions and input rules. References
are not whole-file prerequisites. No read log, limit, new question, or extra
confirmation is required.

| Existing operation or condition | Read when applicable |
|---|---|
| First use or setup required | [Initial Setup](references/task_workflow.md#first-use-and-optional-diagnosis) |
| Explicit optional-feature choices | [Optional Setup choices](references/task_workflow.md#choose-optional-setup-features) |
| Upgrade or migration required | [Upgrade and recovery](references/task_workflow.md#upgrade-and-recovery) |
| Explicit introduction of numerical usage hooks | [Installation and trust](references/usage_hooks.md#installation-and-trust) |
| Missing or incomplete numerical collection | [Collection coverage and recovery](references/usage_hooks.md#coverage-and-recovery) |
| Authorized reviews that outlive the parent turn | [Normal review waiting](references/review_wait.md#normal-wait) |
| Explicit review-wait Setup | [Setup and connection](references/review_wait.md#setup-and-connection) |
| Review-wait failure, stop, diagnosis, or old association | [Wait recovery and compatibility](references/review_wait.md#failures-stop-and-diagnosis) |
| `project_relocation_required` | [Relocation preview and approval](references/cli_contracts.md#relocation-preview-and-approval) |
| Explicit diagnosis or state/package error | [Doctor](references/cli_contracts.md#doctor) |
| Explicit introduction/diagnosis of ordinary-Task host preapproval | [Optional preapproval](references/cli_contracts.md#optional-task-preapproval) |
| Explicit taskization | [Completion-based Task boundaries and registration](references/task_workflow.md#taskize-or-add-scope) |
| Explicit active-Task scope addition | [Scope-addition disposition](references/task_workflow.md#explicit-mid-task-scope-addition) |
| Copy an explicit initial Contract | [Task Contract](references/task_workflow.md#task-contract) |
| Later explicitly authorized Contract change | [Contract revision](references/task_workflow.md#contract-revision) |
| `effort_advisory_enabled=true` | [Optional Effort Advisory](references/task_workflow.md#optional-effort-advisory) |
| A useful continuation boundary | [Optional checkpoint](references/task_workflow.md#optional-continuation-checkpoint) |
| Pause, block, or resume held work | [State transitions](references/task_workflow.md#pause-resume-and-block) |
| A discovery outside accepted scope | [Local handoff](references/task_workflow.md#scope-control-and-local-handoff) |
| Exact review material ready, including `git_snapshot` before commit | [Set the review target](references/task_workflow.md#set-the-review-target) |
| Explicit Receipt/provenance or saved-history investigation | [Task audit](references/cli_contracts.md#task-audit-detail) |
| Explicit trusted-local Runner Plan authoring | [Plan actions, example, and OS limits](references/cli_contracts.md#runner-plan-actions) |
| Maintenance warning after a successful write | [Continuity warnings](references/cli_contracts.md#internal-continuity-boundary) |

For exact options, fields, bounds, and errors, use the matching command in the
[CLI contents](references/cli_contracts.md#contents), not unrelated commands.
Verification without explicit Runner opt-in remains manual.
When ordinary-Task preapproval is explicitly activated, reuse its configured
literal invocation with `--records-only` for ordinary records; generated restricted
handoff commands preserve it. There is no extra permission check command or
per-Task approval. Existing Contract/configuration, Runner, Git and integrated
finalization operations retain their current authorization route.
At registration or an existing edit, state the required verification or use
`--verification-not-required` with an authorized short reason. Omission is
unspecified, not waived, and blocks completion; do not infer a waiver from prose.

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

Read [references/reconciliation.md](references/reconciliation.md#reconciliation-and-test-repair) only when
Effort returns `data.suggested_action=reconcile_scope`, or when a test or review
failure recurs after an attempted repair. One Effort result is one non-blocking
episode, not one per exceeded metric. Neither trigger adds a green-path command,
question, or stop.

## Safety And Privacy

Keep secrets, tokens, authorization data, raw output, stack traces, environment
dumps, private prompts/reasoning, full chat/review transcripts, and large diffs
out of taskgov inputs. If handoff input is privacy-rejected, never repeat,
quote, log, store, or forward the rejected raw content; make at most one fresh
attempt with a concise sanitized abstraction. See [stored-input privacy](references/cli_contracts.md#stored-input-privacy).

Taskgov does not stage files, create branches, push, open PRs, create Issues,
or authorize target/external mutation. The [integrated review finish](references/cli_contracts.md#integrated-review-finalization)
can commit only the fixed reviewed snapshot when project rules and existing
instructions permit it; no approval argument or repeat approval is needed.
Leave canonical offline
projections and backup to explicit setup and bounded same-process maintenance;
they add no LLM command choice or background process. No network use, hidden
acceptance conditions, or project-specific test strategy is added by this Skill.

## License

Original copyrightable package material owned by Omoronine is licensed under
the Apache License, Version 2.0 (`Apache-2.0`). See [LICENSE](LICENSE).
No `NOTICE` is shipped because no concrete attribution duty was identified.
