# Runner Plan Authoring And Control Specification

This document owns the current Runner Plan authoring/control behavior delegated
by the [product specification](specification.md#current-runner-plan-authoring-and-control-contract).
Implementation structure belongs in the
[Runner Plan authoring design](runner-plan-authoring-design.md).
The shared [Plan values and target admission](runner-execution-specification.md#eligibility-plan-and-materialization),
[Task Contract](task-operation-specification.md#task-contract), and
[maintenance](setup-state-specification.md#same-process-maintenance) contracts remain at
their existing owners.

## Current Runner Plan Authoring And Control Contract

This section is the current implementation contract for bounded Runner Plan
authoring. The product retains exactly 21 public command leaves and adds only an
explicit action option to the existing `task edit` leaf. This contract grants no
config write, Task side effect, process launch, target mutation, or external
operation without that invocation. `review target set` remains the sole Runner
dispatch. Schema v22, setup non-generation, current PlanV1 capture/resolution,
Runner execution, Evidence, Viewer, and completion behavior remain unchanged.

### Closed Draft And Actions

Authoring reuses the one existing ignored physical file
`<physical-package>/config/verification-runner.json` and the exact current
PlanV1, EntryV1, and StepV1 member sets. It adds no PlanV2, second config,
per-entry enabled flag, tombstone, setup generation, or Git-tracked plan. The
only new caller document is strict UTF-8 JSON from standard input with the
closed shape:

```text
RunnerPlanDraftV1 = version, steps
```

`version` is exactly integer `1`. The raw stdin document is capped at 65,536
UTF-8 bytes by reading at most one byte beyond the limit before rejection.
`steps` is one through 16 exact StepV1 objects under all existing per-step,
aggregate timeout, literal-argument, and separate 65,536-byte final-plan
bounds. Unknown, missing, duplicate, floating,
non-finite, differently typed, malformed, trailing, empty, or over-bound input
is rejected before mutation. The draft never accepts Task ID, Contract
revision, expectation or criterion digest, coverage, Plan ID, trust, shell,
PATH lookup, review target, or command-discovery input. Taskgov derives the
addressed Task ID and exact future Contract/verification basis and fixes
`coverage="full"`; it does not infer a command, entrypoint, argument, coverage,
test sufficiency, or trust from repository files, project documentation,
verification prose, prior evidence, or an LLM decision.

After bounded UTF-8, duplicate-free JSON, closed-member, and member-type
recognition, every caller-supplied StepV1 string leaf (`step_id`, `mode`,
`entrypoint`, each `argv` item, and `cwd`) is passed unchanged through the
existing common deny-by-default privacy guard under the fixed field label
`Runner Plan draft`. This check precedes that leaf's enum, grammar, UTF-8,
UTF-16, and candidate validation. A privacy rejection for an otherwise
recognized string leaf therefore takes precedence over `invalid_argument` and
returns `privacy_rejected`; malformed or duplicate JSON, unknown or missing
members, non-string member types, and raw-document overflow remain
`invalid_argument`. The guard is draft-input admission only: it neither changes
current PlanV1 reader validation nor reclassifies an existing manually authored
Plan source.

The existing-leaf option is
`task edit <task-id> --runner-plan-action <action>`, where the closed action set
and effects are:

| Action | Standard input | Exact Plan effect |
|---|---|---|
| `replace` | one RunnerPlanDraftV1 | Remove every entry for the addressed Task and insert one future-basis entry with the supplied steps at the earliest removed position, or append it when none existed. It is the only initial-set/upsert action. |
| `rebind` | not read | Require one existing entry for the Task, preserve its steps and position, and replace only its basis with the exact future basis. |
| `detach` | not read | Remove every entry for the addressed Task while preserving all other relative order; absence is an idempotent no-op and an empty entries array remains a valid retained Plan. |
| `disable` | not read | Set only global `trusted_local=false`; preserve Plan ID, every entry, and their order. An absent or already-disabled Plan is an idempotent no-op and no file is created for absence. |

On the first `replace` when the file is absent, taskgov creates PlanV1 with
`version=1`, fixed `plan_id="taskgov-local-plan"`,
`trusted_local=true`, and the one derived entry. On every present Plan,
`plan_id`, unrelated entries, and their order are preserved. `replace`,
`rebind`, and `detach` never change `trusted_local`; in particular, they do not
re-enable a disabled Plan. This contract adds no dedicated re-enable action or
restore workflow. It does not claim that a user cannot later make a separately
authorized direct local edit.

Per-Task cardinality is closed: `replace` and `detach` deterministically repair
zero, one, or multiple distinct-basis entries as defined above; `rebind`
requires exactly one and returns a bounded missing-entry or ambiguity error
otherwise; and `disable` is independent of Task-entry count after structural
Plan validation. An exact duplicate basis remains the existing invalid
`plan_ambiguous` source and no action rewrites it.

The first absent-file `replace` may create only the canonical physical
`config` directory when it is lexically absent, after the package-root,
containment, no-reparse, current-index, and effective-ignore checks succeed.
No other action creates that directory. A publication failure may leave only
that identity-owned empty ignored directory; it is not a Plan source or
authority and is never recursively cleaned as part of authoring.

### One-Invocation Task Coordination

A Plan action may be the only requested mutation, or it may accompany one
actual Task basis change in the same public invocation and under the same user
approval. A basis change is exactly a new Task Contract revision or a changed
Task `verification` value. Title, description, status, priority, kind,
lane/order, tags, notes, review tier, pause/block state, reopen, completion, and
review-target changes do not independently require or authorize a Plan update.

When one valid present Plan has `trusted_local=true` and its sole entry for the
Task exactly matches the pre-edit current basis, a basis-changing edit must
include exactly one of `replace`, `rebind`, `detach`, or `disable`. Taskgov does
not choose an action or automatically carry the entry forward. An absent,
disabled, unreadable, malformed, ambiguous, already-stale, or no-entry Plan
does not hold Task authority hostage: a Task-only edit may still proceed under
the ordinary Task contract and the existing Runner remains fallback or
fail-closed. A requested Plan action still requires a valid source appropriate
to that action.

One invocation may combine a Plan action with other ordinary metadata fields
only when it also produces an actual basis change. Plan-only use supplies no
other Task-edit field. Runner Plan actions are incompatible with done,
completion-evidence, reopen, verification-complete, and review-complete modes;
they never invoke completion's Runner selector. A config-only `replace` or
`rebind` requires a current nonterminal Task and positive Contract plus
verification criterion. Config-only `detach` and global `disable` may address
an existing terminal Task because they perform no Task business write. The
positional Task ID binds project and Task scope; for `disable` it does not make
the global flag task-local.

The direct `task edit` invocation is the mutation authority. An agent should
present the combined Task and Plan disposition before invoking it, but no
mandatory preview command, confirmation token, second approval, or second
normal-path CLI round trip is added. `--read-only` retains its existing
write-rejection semantics.

### Ordered Commit And Partial Success

The coordinator first captures and validates the current Task, Contract,
criterion, Plan source, action, draft, option combination, and expected Plan
identity/digest. For a Task edit it derives the future basis and pure action
result in memory inside the existing bounded Task transaction, including
complete candidate Plan bytes only when normalized semantics change, then
commits and closes SQLite. Only afterward must the publisher revalidate the
expected absent-or-current Plan source for every action. A semantic no-op uses
that boundary only to confirm the expected source and performs no publication;
a changed action publishes the complete candidate by a single-file atomic
replacement. No SQLite writer spans a filesystem read or write.

The Task transaction and Plan publication are two separately committed
operations; they are not one atomic transaction. A Task/DB failure leaves the
canonical Plan unchanged. A detected source drift, failed required source
confirmation, or publication failure after a Task commit never rolls the Task
back and never guesses, merges, or overwrites newer Plan bytes. The requested
Plan disposition is then unconfirmed. Whether the expected source stayed
unchanged or another authorized writer changed it, no eligibility state is
inferred; the Task basis commit itself can change an unchanged entry's prior
exact-match status. The caller must not rely
on Runner execution until one explicit config-only `rebind`, `replace`,
`detach`, or `disable` succeeds. There is no compensating Task edit, two-phase
commit, pending SQLite row, retry daemon, or automatic repair.

The source check is a bounded compare-before-replace guard, not a claim of
cross-process linearizability, power-loss durability, Git/config transaction
atomicity, or zero drift after the final check. Existing target-set admission
remains the final freshness owner. Readers may observe only a complete old or
complete new canonical file; temporary material is bounded, private, and
removed on ordinary failure.

Without a Plan action, `task edit` retains its exact current success and
failure data. With an action, success adds exactly:

```text
runner_plan_update = action, status
action = replace|rebind|detach|disable
status = updated|unchanged|unconfirmed
```

`unchanged` means the pure action produces the same normalized Plan semantics,
including absent-source or missing-entry `detach`, absent/already-disabled
`disable`, and an otherwise identical replacement or rebind. A semantic no-op
has no candidate publication, does not normalize valid noncanonical raw bytes
as a side effect, and returns `unchanged` only after post-transaction
confirmation that the expected source still matches. `updated` means changed
normalized semantics were successfully published as complete canonical bytes.
`unconfirmed` is available only after an actual Task commit followed by an
unsuccessful required Plan source confirmation or publication.

Plan-only success returns the current Task, empty `changed_fields`, null
`event`, no Task/Contract write, and `updated|unchanged`. If required source
confirmation or publication fails after an actual Task commit, the command is
a successful partial result with the committed Task/event,
`status=unconfirmed`, and exactly one Runner-Plan-originated bounded warning:

```text
task_applied_runner_plan_unconfirmed
Task update completed but Runner Plan disposition is unconfirmed; apply an explicit Plan action before relying on Runner execution
```

Ordinary post-commit maintenance still receives exactly one opportunity for
that committed Task mutation. Its existing zero through three maintenance
warnings, when any, follow the one authoring warning in their existing order.
A pre-commit failure or config-only source-confirmation/publication failure
remains an ordinary failed command with the existing empty Task-edit failure
data and no maintenance. An action-bearing text success appends exactly
`Runner Plan: <action> <status>` after the existing Task/Contract lines; an
invocation without an action remains byte-compatible.

The new authoring failure map is closed:

| Condition | Code / exit | Fixed public message |
|---|---|---|
| caller StepV1 string rejected by the common privacy guard | `privacy_rejected` / 1 | `Runner Plan draft appears to contain a secret, raw log, or dump content` |
| invalid action, stdin draft, candidate, or caller value | `invalid_argument` / 1 | `arguments are invalid` |
| incompatible Task-edit options | `invalid_option_combination` / 1 | `Runner Plan action cannot be combined with these task edit options` |
| required disposition omitted for an enabled exact-match entry | `runner_plan_action_required` / 1 | `Runner Plan action is required for this Task basis change` |
| `rebind` has no Task entry | `runner_plan_entry_required` / 1 | `Runner Plan entry is required for rebind` |
| unsafe, malformed, over-bound, duplicate-basis, or ambiguous source | existing `plan_source_invalid|plan_invalid|plan_too_large|plan_ambiguous` / 2 | existing sanitized Plan message |
| expected source changes before a config-only confirmation or publication | `runner_plan_changed` / 2 | `Runner Plan changed before update; no Plan change was made` |
| another config-only source-confirmation or publication failure | `runner_plan_update_failed` / 2 | `Runner Plan update did not complete` |

After a Task commit, any later Plan source-revalidation, drift, or publication
failure maps instead to the successful unconfirmed partial result above.
Existing Task, Contract, database, and storage errors keep their current
mappings. No response, warning, event, log,
database, Evidence, Viewer, or history projection contains Runner Draft/Plan
bytes, steps, argv, publisher paths, rejected input, or publisher exception
detail.

### Disable, Evidence, And Activation Boundary

`disable` affects only admission of future attempts; it neither cancels an
in-flight process nor deletes entries, canonical files, Runner graph rows,
observations, References, Bundles, completion cycles, or history. `detach`
removes every entry for the addressed Task from the current Plan and no other
entry. A Task basis change makes older Runner
observations historical; it never converts them into a current Receipt or lets
them satisfy a new current gate.

Authoring never calls `review target set`, captures a review target, launches a
process, creates a Receipt or Runner graph, or changes the existing
target-plan/process/lifecycle/native/completion/Evidence/Viewer paths. The
activation revision synchronizes `AGENTS.md`, the public CLI, active
specification/design/plan, CLI contract reference, README opt-in examples,
package manifest, and focused tests. The Skill and normal Task loop remain
unchanged. TG-RPA.6 performs acceptance-only full offline validation; a
correction returns to its owning predecessor Task.
