> [!CAUTION]
> **NON-AUTHORITATIVE HISTORY**
>
> This capture preserves completed M25 execution narrative, not current
> product behavior, implementation authority, live Task state, or evidence.
> Internal words such as current, approved, accepted, active, or implemented
> describe only the captured revision. This file cannot fill a current
> authority gap or satisfy a current gate.

# M25 Select-Split-Merge-Register Execution Narrative Capture

- Source path: `plan.md`
- Source commit: `656bac13a95d78ff142c7df221169536256ac0e6`
- Capture unit: `TG-RMAP.2`
- Current replacements:
  [specification](../../specification.md#current-m25-select-split-merge-register-contract),
  [design](../../design.md#current-m25-select-split-merge-register-design),
  [SKILL.md](../../../task-governance-tool/SKILL.md),
  [Task workflow](../../../task-governance-tool/references/task_workflow.md),
  and [plan](../../../plan.md#m25-select-split-merge-register).
  Use the public CLI for live Task state and evidence.
- Capture purpose: preserve the complete pre-compaction M25.1/M25.2 Task
  ordering, activation, accepted behavior summary, and one-time boundary
  narrative from the committed source.

The exact source section begins below and ends before the next plan anchor.

````markdown
<a id="m25-select-split-merge-register"></a>

### M25 Active Select-Split-Merge-Register Guidance

M25.1, Task `tg_task_8e33e15cd97a28ee`, in lane
`TG-M25-TASK-DECOMPOSITION` at order 10, is sequenced after TG-RPA.6; the Task
database owns fulfillment of that gateway. It is a Tier 2 docs-only unit for
two explicit authority events: a request to register or taskize authorized
work, and an explicit scope addition to an in-progress or review-pending Task.
M25.2, Task `tg_task_d891cd538d9e7364`, at order 20 activates that accepted
contract only in Skill and task-workflow guidance after M25.1; the Task database
owns fulfillment of the activation and its review gates.

The accepted design selects stable responsibilities, performs one flat Split,
then one global Merge of all fragment-only transitive groups and never
re-Splits. Final slices conserve exact scope and permissions, declare consumed
inputs and produced outputs, leave a correct ordered repository state, carry
locally attributable verification/review, and fit a fresh-agent context without
requiring standalone user value. Sharing files, tests, commands, or fixtures
alone does not force Merge.

Contracts copy explicit authority only. Missing split or Contract detail falls
back to one whole-outcome revision-zero Task when outcome and permission are
clear; exact mandated-boundary conflicts or unclear outcome/permission use one
grouped question and no partial write. Partial-add recovery preserves successful
registrations and adds only proven omissions without deletion or repartition
while the same event retains its transient final set. After interruption loses
that set, no reconstruction occurs; current explicit authority governs a
grouped question when the exact remainder or permission is unclear, or one
confirmed revision-zero remainder Task when both are clear.

Binding authority supplies a Review Tier floor. With no mapping, the closed
fallback is Tier 2 for schema/migration, JSON contract, CLI write behavior,
target mutation, privacy/logging, Skill trigger, verification/review/completion
gate, milestone/plan acceptance, and implementation-binding normative docs;
Tier 0 for wholly mechanical meaning-preserving work; and Tier 1 otherwise.
Unknown, size, difficulty, duration, failure count, safety wording, or reviewer
availability never alone selects Tier 2. Mid-Task keep preserves Tier, revise
raises only to a higher resulting floor and never auto-lowers, a successor uses
its own scope, and Merge into current uses the maximum. A required Tier raise
precedes Contract revision, so failure cannot leave expanded scope below its
floor. Later integration review never replaces each Task's required gate.

Design-first registers no automatic implementation Task. M25.2 activates only
the two explicit Skill routing events and adds no command, schema, JSON or
database field, Viewer/Runner change, parent/child or dependency model,
normal-loop call, network use, target mutation, or automatic execution unit.
The exact Task DB record owns live state.
````
