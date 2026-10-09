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
complete-Packet transport or an unclear role, use the conditional
[reviewer procedure](references/task_workflow.md#independent-reviewer).

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

Use the [review workflow](references/task_workflow.md#prepare-and-record-reviews)
for ordinary shared-file handoff; use [direct transport](references/task_workflow.md#direct-review-transport)
only when passing complete Packet/result bytes instead. Assigned independent
reviewers use the separate entry above.

Read only the linked responsibility needed for the operation or returned
condition, including its applicable exceptions and input rules. References
are not whole-file prerequisites. No read log, limit, new question, or extra
confirmation is required.

| Existing operation or condition | Read when applicable |
|---|---|
| First use, upgrade, or setup/migration required | [Setup and diagnosis](references/task_workflow.md#first-use-and-optional-diagnosis) |
| Explicit introduction or diagnosis of numerical usage hooks | [Optional collection hooks](references/usage_hooks.md#optional-usage-collection-hooks) |
| Authorized reviews that outlive the parent turn | [Normal review waiting](references/review_wait.md#normal-wait) |
| Explicit review-wait Setup | [Setup and connection](references/review_wait.md#setup-and-connection) |
| Review-wait failure, stop, diagnosis, or old association | [Wait recovery and compatibility](references/review_wait.md#failures-stop-and-diagnosis) |
| `project_relocation_required` | [Relocation preview and approval](references/cli_contracts.md#setup) |
| Explicit diagnosis or state/package error | [Doctor](references/cli_contracts.md#doctor) |
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
attempt with a concise sanitized abstraction. See [input errors and privacy](references/cli_contracts.md#errors-and-privacy).

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
