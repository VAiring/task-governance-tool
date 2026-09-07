# Task Operation Section Split Capture

> [!CAUTION]
> **NON-AUTHORITATIVE HISTORY**
>
> This capture preserves only the Task operation sections before their document
> split. Words such as current, approved, or implemented describe the captured
> revision, not current authority. This history cannot fill an active-contract
> gap, satisfy a current gate, or authorize a removed behavior.

- Source commit: `7fc372423b7d97c04a3108215c22ceaf578e3586`
- Source paths: `docs/specification.md` and `docs/design.md`; the exact
  section boundaries are identified with each captured block below.
- Capture unit: `TG-MOD.10`
- Active replacements:
  [Task operation specification](../../task-operation-specification.md)
  and [Task operation design](../../task-operation-design.md).
  [Repository authority](../../authority.md) routes the unchanged common owners.
  Use the public CLI for live Task state and evidence.

## Captured Section 1: Task Selection And Read Commands

Source path: `docs/specification.md`

Source range: `### Task Selection And Read Commands` through immediately before `### Doctor Contract`.

````markdown
### Task Selection And Read Commands

Task statuses are `ready`, `in_progress`, `paused`, `blocked`,
`review_pending`, `done`, and `cancelled`; priorities are `low`, `normal`,
`high`, and `urgent`; kinds are `sequential` and `optional`; review tiers are
0, 1, and 2.

`task add` requires a title and accepts description, kind, lane/order,
priority, initial status, blocker reason, review tier, verification, tags, and
the optional complete Task Contract group. Defaults are optional kind, normal
priority, ready status, and Tier 1. A sequential Task may receive a
deterministic default lane and append order; output exposes the stored values.
Initial blocked requires a reason. Initial done and paused fail respectively
with `initial_done_forbidden` and `initial_paused_forbidden`.
The exact editable Task arguments are title, description, kind, lane, order,
priority, status, blocked/pause reason, review tier, verification, tags, note,
reopen reason, review-tier-change reason, typed/legacy completion evidence and
confirmations, and the Contract group. A write response states the fields and
event it recorded.
The corresponding exceptional options are exactly `--reopen-reason`,
`--review-tier-change-reason`, `--completion-evidence-kind`,
`--completion-revision`, `--completion-evidence-reason`,
`--external-revision-approved`, `--completion-commit-hash`,
`--commit-not-required`, `--verification-complete`, and
`--review-complete`.

`task list` is a compact bounded read with status, kind, lane, priority, tag,
limit, and include-done filters. Its default limit is 20 and maximum is 100;
order is priority urgent/high/normal/low, canonical lane, lane order with nulls
last, creation time, then Task ID. `task next` returns only ready optional
Tasks and ready sequential Tasks whose earlier same-lane Tasks are done or
cancelled. Its filters are kind, lane, priority, and limit; default limit is 5.
Order is priority urgent/high/normal/low, canonical lane, lane order with nulls
last, creation time, then Task ID. Paused, active, blocked, review-pending,
done, and cancelled Tasks are excluded.

When paused work exists, successful `task next` adds exactly one
`paused_tasks_present` warning with the exact paused count and suggestion
`taskgov task current --status paused`. It does not change candidates, data,
exit status, or state. Zero paused Tasks or a failing next command emits no
such warning. The count and candidate selection may be separate committed read
transactions; each is coherent, but no cross-transaction linearizability is
claimed.

`task current` returns only `in_progress`, `review_pending`, `paused`, and
`blocked`, with the latest event, reasons, update time, and deterministic next
action. Default limit is 20 and maximum is 100. Order is status in that listed
order, priority urgent/high/normal/low, newest `updated_at`, then Task ID.
Optional `--status` accepts only one current-work status; JSON reports the
effective `statuses`. It is bounded rediscovery, not stale-age, working-tree
freshness, exhaustive history, or pagination.

Only `task current` and `task next` accept `--compact`, and compact requires
`--json` or fails before state access with `invalid_option_combination` and
exact message `--compact requires --json`. Compact current is at most 24,576
UTF-8 bytes and compact next at most 16,384. Rows remain in deterministic
order; the first row that would cross the cap and all later rows are omitted
and `truncated=true`. Event summary is at most 256 UTF-8 bytes at a code-point
boundary.

Compact current data is exactly `tasks`, `total_matching`, `returned_count`,
`limit`, `statuses`, and `truncated`; each Task contains `task_id`, `title`,
`status`, `kind`, `lane`, `lane_order`, `priority`, `review_tier`,
`blocked_reason`, `pause_reason`, `latest_event`, and
`suggested_next_action`. A compact event contains `event_type`, `summary`,
`created_at`, and `summary_truncated`.

Compact next data is exactly `tasks`, `total_matching`, `returned_count`,
`limit`, and `truncated`; each Task contains `task_id`, `title`, `kind`,
`lane`, `lane_order`, `priority`, `review_tier`, `tags`, and
`suggested_next_action`.

`task show` reads one Task, bounded events, current review evidence, Contract,
handoff counts, latest checkpoint, completion history, and suggested action in
one query-only transaction. It also returns exactly one routing Boolean
`effort_advisory_enabled`; invalid advisory configuration returns false plus
the existing continuation warning. Text show does not add that flag.

Every Task-loading operation applies the current stored-row and
Contract-relationship contracts
before an allow-list projection, compact omission, derived-state use, or
write-basis use. Bounded list/current/next reads validate the complete rows in
their selected batch and do not add an unrelated full-table scan. `task show`
and Task-backed lifecycle operations validate the selected complete row before
reading or mutating dependent state. The shared failure result is defined in
the current stored-Task validation sections below.

The deterministic Skill call graph is:

- one compact `task current` call to rediscover work;
- when it returns an `in_progress` or `review_pending` row, resume the first
  such row in returned order; otherwise make one compact `task next` call.
  Returned `paused` and `blocked` rows remain rediscovered but do not suppress
  unrelated ready selection;
- one `task show` call for the resumed or selected task so its complete current
  Contract, latest checkpoint, and Effort Advisory routing flag are always
  read;
- one task edit to start the selected task;
- only for a deterministically enabled Effort Advisory profile, one existing
  `task effort` observation at the verification/review boundary;
- one review target set call after the exact material is ready; its returned
  `verification_route` and `blocking_code` deterministically select the
  not-required, Receipt-required, qualifying Runner-pass, or blocking branch;
- only for `verification_route=receipt_required` on the marker-`0` manual branch
  or exact closed no-launch fallback, one `verification receipt add` call after
  the caller runs the complete governed verification against that exact target;
  the not-required and qualifying Runner-pass branches need no Receipt call;
- one `review prepare` call instead of separate task, Contract, target, and Git
  context reads;
- one receipt write per actual receipt; and
- one thin complete call.

A default-off no-finding Tier 2 manual/fallback path therefore has at most
ten governance subprocess calls; a profile-enabled path has at most eleven.
The qualifying Runner-pass path omits Receipt add and remains bounded to nine or
ten calls respectively. All counts exclude real progress updates and the two
independent review model decisions.
`task complete --check`, `doctor`, and `task checkpoint` are absent from the
default success path.

````

## Captured Section 2: Task State, Scope, Review, And Completion

Source path: `docs/specification.md`

Source range: `## Task State, Scope, Review, And Completion` through immediately before `## Approved Post-MVP Extension: TG-M16 Reduced Loop Discipline Trial`.

````markdown
## Task State, Scope, Review, And Completion

### Task Record And State Transitions

The current Task projection includes stable Task/project IDs, title,
description, kind, lane/order, priority, status, blocker/pause reason, review
tier, verification, tags, timestamps, current typed completion evidence plus
its legacy compatibility projection, current review target/generation/base,
Contract pointer, and completion-history coverage. Concise events retain notes
and transitions; events are not a generic payload/log store.

Typed completion storage is exactly `completion_evidence_kind`,
`completion_evidence_revision`, `completion_evidence_reason`,
`external_revision_approved`, `completion_commit_required`, and
`completion_commit_hash`. Current target storage is
`review_target_kind`, `review_target_value`,
`review_target_base_revision`, and generation. Values and their legacy
projection must satisfy one cross-field matrix before storage or output.
For a supported schema-v18-through-v22 source, every complete loaded Task row is validated
for exact SQLite/Python storage class, bounded text/privacy, closed enums, and
all Task cross-field matrices before any field can be omitted or exposed.
Stored values are never coerced, trimmed, repaired, or rewritten by a read.

Optional work is actionable when ready. Sequential work is actionable only
when every earlier Task in its lane is done or cancelled. That same predicate
guards entry into `in_progress`, `review_pending`, or `done`; paused is
incomplete. Add/edit validates the complete resulting row and both affected
lanes when kind/lane/order/status changes. It rejects inserting or moving an
incomplete predecessor before active, review-pending, or done work with
`sequential_predecessor_incomplete` and no event/write. Blocked lanes never
hide unrelated ready optional work or other ready lanes.

Blocked requires a concise reason. Paused requires a concise reason and is
reachable only from in-progress or review-pending; normal resume is paused to
in-progress. Initial paused is prohibited. Lane values are trimmed, and
explicit/automatic order must fit signed 64-bit.

A done Task is write-locked. Every write except an exact reopen fails
`done_task_requires_reopen`. Reopen alone requires a sanitized reason, changes
done to in-progress, clears completion/blocker/pause/current target, advances
review generation, preserves all history, appends `task_reopened`, applies the
lane guard, and cannot share metadata, note, evidence, confirmation, or other
mutation. Fresh verification, target, review, and completion evidence are then
required.

A review-tier downgrade is allowed only while ready, in-progress, paused, or
blocked, before structured review (`generation=0` and target empty), with a
reason, and without completion evidence, gate confirmation, or transition to
review-pending/done. Once review begins, including after reopen, tier may only
stay or rise. Invalid downgrade is `review_tier_downgrade_forbidden`.
Successful change appends `review_tier_changed` with old/new tier and reason.

### Task Contract

The optional Task Contract copies already-explicit authority and adds no
question or heuristic. Revision 1 is allowed only when scope and acceptance
are explicit in the current user instruction, approved execution plan or
execution-unit set, or explicit registration input. Supplying any of
`--contract-scope`, `--contract-acceptance`, `--contract-constraints`,
`--contract-authority-ref`, or `--contract-change-reason` supplies the group;
partial explicit input fails `invalid_argument`.

Revision 1 may be created on add with status ready, in-progress, blocked, or
review-pending, or in one revision-0 transition from ready/blocked to
in-progress. The edit boundary additionally requires empty completion
evidence, target, and generation, and cannot share note, metadata, review-tier,
completion, or confirmation input. Paused/review-pending/done/cancelled Tasks
cannot activate revision 1. Omitted Contract input leaves revision 0 without
making acceptance optional or prompting for fields.

A Contract stores scope and acceptance (each at most 4,000 characters),
optional constraints (2,000), optional initial stable authority reference
(500), and for later revisions a reason (1,000) and timestamp. Purpose remains
in title/description. Text normalizes line endings to LF and outer whitespace;
internal content is preserved.

Later semantic revision is Contract-only, allowed while ready, in-progress,
paused, blocked, or review-pending, and requires at least one changed content
field, a nonempty stable authority reference, and reason. User authority may
use `user_instruction:<task-id>:<next-revision>`; governing authority uses a
repository-relative path plus known revision/hash. Neither stores prompt/body.
A current Task output cannot authorize expansion of that same Task.
Out-of-scope hardening is handed off.

An exact canonical content replay is a successful write-free no-op even if
authority/change labels are omitted or relabeled after their privacy/size
validation. It returns `recorded=false`, current revision, `event=null`, and
`changed_fields=[]`. Same-content concurrency produces one row plus one
replay. Different valid semantic writes serialize; pointer races fail
`contract_write_conflict`.

On later revision, omitted constraints preserve the current value and an
explicit empty value clears it. The exact
`user_instruction:<task-id>:<revision>` form is validated mechanically.
Semantic change accepts only the locked next revision placeholder; exact replay
may accept an older same-Task positive placeholder. A caller-supplied authority
label never creates a revision when Contract content did not change. Omission
may therefore carry forward byte-identical, already-validated legacy M19.7
constraints as immutable lineage; it does not validate caller-supplied legacy
vocabulary or grant new authority. Any explicitly supplied constraints use the
normal strict input guard.

A semantic revision appends immutable history, advances the pointer, clears
completion evidence, preserves generation 0 if review never began or otherwise
clears target and advances generation, moves review-pending to in-progress,
updates time, and appends `contract_revised` atomically. Fresh gates are
required. Done must reopen; cancelled rejects.

Only `task show` exposes the full additive `contract` object: revision, scope,
acceptance, constraints, authority reference, change reason, and creation time.
Revision 0 uses empty strings and null time. Compact/list/current/next/Viewer
omit Contract text.

### Handoff Outbox

For every discovery, classify once:

1. Keep it in the Task when resolution is within accepted scope and current
   authority.
2. Block the affected Task when an unmet condition prevents acceptance and no
   safe authorized resolution remains.
3. Otherwise run the same `handoff record` command immediately and continue.

Safety is orthogonal: report credible risk promptly, block only unsafe affected
work, and continue other safe ready lanes. Pending handoffs never expand or
block source acceptance.

`handoff record` first commits one local sanitized record regardless of Issue
adapter presence. States are only `pending_handoff`, `handed_off`, and
`handoff_withdrawn_by_user`; allowed transitions are pending to either terminal
state. Withdrawal means the user handled/withdrew undelivered work, not Issue
resolution, and is forbidden after any delivery claim.

The outbox stores source Task/Contract revision, bounded summary/rationale,
optional occurrence ID, stable idempotency identity, state, bounded delivery
bookkeeping, and timestamps. It stores no Issue priority/lifecycle/triage,
semantic duplicate decision, `resulting_task_id`, threat model, raw
output/review, secret, stack trace, or diff. Public records never expose an
internal `claim_token`.

Exact source/canonical-payload replay returns the same record. A separate
occurrence requires an explicit stable ID; omission is canonical empty.
Invalid/empty explicit/over-200 occurrence ID is
`handoff_occurrence_invalid`. Summary and rationale are at most 1,000
characters.

Local commit is the success boundary. It may retry one complete fresh
transaction after transient SQLite failure and may replace rejected content
once with a shorter sanitized abstraction. Persistent failure returns
`handoff_not_persisted`, never claims durability, and stops the current unit
until persistence or explicit acceptance of forgetting risk. Delivery absence
or failure after local commit leaves pending and source work continues; enabled
delivery failure adds warning `handoff_delivery_pending` with action
`continue`.

Public leaves are record/list/show/withdraw; base Skill has no dead sync
command. List defaults to pending, oldest `created_at, handoff_id`, limit 20,
maximum 100, and returns exact `total_matching` from the same read snapshot.
Compact rows contain only `handoff_id`, `source_task_id`,
`source_contract_revision`, `summary`, `state`, `created_at`, and
`updated_at`. Full public records never expose internal claim tokens and are
revalidated before output. `task show.handoff_summary` contains exact counts
for all three states.
Successful withdrawal reports
`changed_fields=["state","withdraw_reason","withdrawn_at"]`.

Adapter delivery is not implemented and remains disabled until a separately
approved versioned local Issue intake exists. Doctor therefore reports the
local pending count with adapter/due false. Task Skill never opens an Issue
database, shells, uses a URL/network/GitHub, imports, reconciles, prioritizes,
or mutates Issue lifecycle. Any later adapter must preserve the same local
record command and source-task non-blocking boundary.

### Effort Advisory

The optional project-scoped profile exists only at
`config/effort-advisory.json`; taskgov never creates or edits it. Strict
profile v1 requires `profile="informational-v1"` and Boolean `enabled`.
Optional nonnegative integer thresholds are limited to `changed_files`,
`changed_lines`, `changed_modules`, `contract_revisions`, and `handoffs`, and
exceed only when measurement is greater than the threshold. Unknown/duplicate
keys or invalid values disable the profile with a bounded diagnostic.

`task effort <task-id>` reports those five deterministic metrics when covered.
Git measurement is read-only; Contract/handoff counts use structured DB data.
Missing coverage or dirty/uncertain/non-Git endpoints and overlapping active
work produce `unknown`, never inference. Optional basis capture on the first
in-progress write is best-effort and never blocks start. An absent/disabled
profile performs no Git work; disabling after a prior basis may retain only
hidden activity counters.

The basis captures project/subject activity generations and
`other_active_at_capture`; later attribution is exclusive only when both
endpoints and generation bridge remain reliable. A bounded observation failure
may report `activity_generation_uncertain`. These fields are advisory
bookkeeping, never Task authority or a completion gate.

````

## Captured Section 3: Approved Post-MVP Extension: TG-M16 Reduced Loop Discipline Trial

Source path: `docs/specification.md`

Source range: `## Approved Post-MVP Extension: TG-M16 Reduced Loop Discipline Trial` through immediately before `## Review And Completion`.

````markdown
## Approved Post-MVP Extension: TG-M16 Reduced Loop Discipline Trial

The nonempty deterministic `exceeded` list is the sole predicate that changes
an enabled valid result's `data.suggested_action` and matching threshold-warning
action from `continue` to `reconcile_scope`. Absent, disabled, invalid,
unknown-only, and non-exceeded observations continue. The warning remains
`effort_advisory_threshold_exceeded`, key
`effort_advisory.threshold_exceeded.v1`, and message
`One or more configured effort thresholds were exceeded.` Any number of
exceeded metrics creates at most one session-local episode.

The signal is non-blocking. It never asks the user, writes a handoff, changes
Task status, Contract, acceptance, review tier/evidence, completion evidence,
pauses, blocks, or fails a Task. It adds no second observation, command, or
green-path judgment.

Reconciliation guidance is session-local. Without new evidence, after two
materially equivalent failed repair attempts the agent must not execute a
third equivalent repair. Command spelling, working directory, wrapper, Task
label, or execution-unit label alone is not new evidence. A safe diagnostic or
genuinely different repair is new only when it can materially change the
causal hypothesis, authorized repair, or expected outcome.

Never weaken a test merely to pass. A test may change only when current
authority shows it is wrong; a Task Contract or acceptance change still needs
later explicit authority. A failing test alone proves neither scope nor
authority. Work inside accepted scope/current authority remains in the Task;
out-of-scope work is handed off; a blocker is recorded only after safe
authorized work is exhausted; paused is reserved for temporary interruption;
unrelated safe ready lanes continue and remaining decisions are batched.

Review repair still requires a fresh target and fresh current-generation
review. A result that remains blocking counts as one unsuccessful remediation
cycle. Without new evidence, two materially equivalent unsuccessful review
cycles prohibit a third equivalent cycle. Completion still requires qualifying
fresh PASS receipts; unrelated safe lanes continue.

No attempt counter, persisted latch, semantic-failure parser, automatic
Task/Contract/status/handoff mutation, mandatory checkpoint, project test
strategy, instruction-chain adoption, or workflow engine is added. Setup
creates no bootstrap Task and edits no consuming-project instruction.

````

## Captured Section 4: Typed Checkpoint

Source path: `docs/specification.md`

Source range: `### Typed Checkpoint` through immediately before `## Completion Cycle History`.

````markdown
### Typed Checkpoint

`task checkpoint <task-id>` requires summary and next action and accepts up to
eight unresolved risks. UTF-8 limits are 1,024 for summary, 1,024 for next
action, 512 per risk, 4,096 combined risks, and 6,144 total caller payload.
One append-only row stores only that content, Task/project, Contract revision,
and time. The same transaction adds event type `checkpoint_recorded` with
fixed summary `Checkpoint recorded`, does not copy content into the event, and
does not update `tasks.updated_at`.

Exact replay against the latest same-Contract checkpoint is write-free with
`replayed=true`. Done is immutable. Checkpoints are optional, never automatic,
and change no status, scope, acceptance, selection, review, evidence, or gate.
`task show`/default current expose only the latest object.

The stored-summary read path alone retains bounded compatibility for the
already-recorded M19.7 numeric `dispatch_authorization` JSON field and returns
the original summary unchanged. New checkpoint input uses the normal strict
guard, and the compatibility read neither records nor authorizes an external
operation.

Command data is exactly `checkpoint`, `created`, `replayed`, and `event`.
Checkpoint keys are `checkpoint_id`, `task_id`, `contract_revision`,
`summary`, `next_action`, `unresolved_risks`, and `created_at`. New event
output contains only ID, type, and time; replay event is null. Text is
`Checkpoint <checkpoint_id>: recorded|replayed for task <task_id>\n`.

````

## Captured Section 5: Current M25 Select-Split-Merge-Register Contract

Source path: `docs/specification.md`

Source range: `## Current M25 Select-Split-Merge-Register Contract` through immediately before `<a id="current-schema-v21-verification-ledger-and-bundle-contract"></a>`.

````markdown
## Current M25 Select-Split-Merge-Register Contract

M25.1, Task `tg_task_8e33e15cd97a28ee`, froze design authority for two and
only two explicit user-authority events: an instruction to register or taskize
already-authorized work, and an explicit scope addition to an `in_progress` or
`review_pending` Task. M25.2, Task `tg_task_d891cd538d9e7364`, activates that
contract only in current Skill and task-workflow guidance. Discovery, a test
failure, an Effort result, task size, or model preference does not create either
event.

### Candidate-First Split And One Global Merge

The Skill instruction layer first fixes one authority envelope containing the
complete authorized outcome, its permission boundary, any binding order, and
any explicit Contract or Review Tier mapping. It then performs one flat
candidate-first Split at stable responsibility boundaries. A provisional
candidate states one bounded responsibility, its authorized consumed inputs and
produced outputs, and any concrete fragment-to-owner coupling. It may expose
that it cannot yet stand as a Task; that fact is input to the one global Merge,
not a reason to reject the candidate before Merge.

After that Merge, each final slice must:

- own one bounded final responsibility with its authorized consumed inputs and
  produced outputs;
- leave the repository in a correct state when completed after its represented
  predecessors;
- have verification and review whose result is locally attributable to that
  responsibility;
- use the existing sequential/optional lane and order model; and
- be resumable by a fresh agent from its Task Contract, routed authority, and
  declared predecessor outputs without relying on prior chat or a hidden
  worksheet.

A slice need not deliver standalone user value. Shared files, tests, commands,
or fixtures do not alone prevent separate slices. File count, line count,
estimated effort, duration, risk wording, or implementation steps do not create
a responsibility boundary.

The flat candidates must conserve the authority envelope exactly: their scope
union is complete and non-overlapping, their permission union preserves the
complete explicitly authorized permission envelope without omission, no group
exceeds that original boundary, and their ordering adds no unapproved outcome.
The instruction layer then performs one global Merge pass over the complete
flat set. Every fragment-only candidate is merged with the responsibility that
consumes its output or owns its acceptance. A candidate is fragment-only when it
cannot leave a correct repository state, cannot carry attributable local
verification and review, or expresses only part of an inseparable
responsibility. Concretely coupled fragments form their transitive groups in
that same pass, which may merge several disjoint groups at once. Ambiguous
ownership uses the fallback below rather than an arbitrary Merge. The pass does
not merge candidates merely because they share files, tests, commands, or
fixtures.

The merged set is final for that explicit event. It is never Split again,
recursively decomposed, or optimized through a second Merge pass. If a valid
flat set cannot be formed, the registration and grouped-question fallbacks
below apply instead of inventing another boundary. A reply, clarification,
paraphrase, or answer about the same taskization or scope-addition outcome stays
in the same event; only materially changed authority for scope, order, or
permission starts a new event.

### Explicit Registration And Contract Population

An explicit request to register or taskize work authorizes one existing
`task add` write for each final group. It authorizes no implementation,
target-project mutation, Git or network operation, external delivery, or
permission expansion. Each non-zero Contract copies only scope, acceptance,
constraints, and authority reference that the governing sources or user
instruction state explicitly. It never infers acceptance or permissions merely
to make a split look complete.

When the authorized outcome is clear but the authority lacks the detail needed
for a truthful multi-Task Split or complete non-zero Contracts, register one
whole-outcome revision-zero Task. Revision zero records that Contract detail is
still absent; it does not prove or invent that detail. This is the normal
fallback and does not trigger one question per candidate.

There is one grouped-question boundary. Ask one question containing all and
only the missing facts when the user mandated separate boundaries that cannot
be represented truthfully, or when the outcome or permission boundary is too
unclear to register even one honest whole-outcome Task. Do not register a
partial interpretation before that answer. Missing size, implementation, path,
or test detail alone does not cross this boundary.

If several final groups are being added and the existing `task add` sequence
fails after a strict subset succeeds, stop the pass and inspect the exact
registered set through existing reads. While the same uninterrupted event still
holds the transient final set, preserve the authorized remainder and, after the
ordinary failure is resolved, add only groups proven missing. Never delete a
successful registration, duplicate one, repartition the set, widen a Contract,
or restart Select-Split-Merge.

If the transient final set is lost before recovery, do not reconstruct or rerun
it. Preserve a bounded Handoff summary when its existing limits can state
the unregistered outcome truthfully, then require current explicit authority
before any new write. If the exact remainder or permission boundary is no longer
clear, use the one grouped question above; a confirmed whole remainder may use
one revision-zero Task, without disturbing successful registrations.

### Review Tier And Design-First Rules

A binding authority mapping sets the minimum Review Tier for the scope it
governs. Ordinary Task registration, partitioning, or wording cannot lower that
floor; changing the mapping is an explicit governance change. With no binding
mapping, use this closed fallback:

| Scope delta | Review Tier floor |
|---|---|
| Schema or migration; JSON contract; CLI write behavior; target-project mutation; privacy or logging; Skill trigger; verification, review, or completion gate; milestone or plan acceptance; implementation-binding normative documentation | Tier 2 |
| Wholly mechanical and meaning-preserving work | Tier 0 |
| Every other bounded scope | Tier 1 |

Unknown facts, size, difficulty, duration, failure count, safety wording, or
reviewer availability never alone selects Tier 2. Each final group receives its
own applicable floor. An explicitly authorized higher Tier is allowed, but
ordinary registration input cannot lower the floor. A later integration review
never replaces that Task's required review.

When explicit authority or an implementation-binding owner requires a design
decision before an implementation Contract can be truthful, register only the
bounded design responsibility unless existing authority already states both
truthful ordered slices. Its produced decision becomes declared authority and a
consumed input only for a later explicit registration event; design-first never
invents an implementation Task. If already-authorized design and implementation
responsibilities cannot each meet the slice conditions above, the global Merge
keeps them together.

### Explicit Mid-Task Scope Addition

For one explicit addition to an `in_progress` or `review_pending` Task, first
preserve the scope and permission envelope, then apply Select-Split-Merge once
to the addition and its relationship to the current responsibility:

- an addition already wholly covered by the current Contract is `keep-current`;
  it makes no Contract write and preserves the current Review Tier;
- an addition that remains in or globally Merges into the current
  responsibility has the maximum of the current Tier and every applicable
  resulting floor. When that value is higher, raise the Tier through its
  existing edit before using the existing semantic Contract-revision path; it
  never auto-lowers. Review does not advance between the two writes. A failed
  Contract write can therefore leave only unchanged scope at a conservatively
  higher Tier, never expanded scope below its floor;
- a final successor group uses the floor for its own scope, not the current
  Task's Tier. Registration requires an explicit taskization or separate-Task
  direction; otherwise present one proposal without a Task write; and
- an addition that cannot yet be placed truthfully uses the existing bounded
  Handoff path, followed by continue, pause, or block under their existing
  preconditions. Status never substitutes for preserving authorized scope.

A current-before-successor relationship must fit existing lane/order. Moving
already-covered work to a successor requires explicit repartition authority.
Contract revision retains all existing target/evidence invalidation effects;
`keep-current` retains evidence only while the ordinary exact-target rules do.
An unresolved proposal is not persisted as a new model or reconstructed from a
Handoff. The same explicit addition event never starts another Split after its
global Merge.

### Active Instruction-Layer Boundary

M25 Select-Split-Merge-Register is active only in current `SKILL.md` and
`references/task_workflow.md`. It changes no public command, normal Task-loop
call count, SQLite schema, JSON contract, Viewer field, automatic Task creation,
runtime Task splitting, parent/child or dependency model, background LLM work,
network behavior, or target-project mutation. Its grouping and tier-basis
reasoning remain session-local and are not persisted as a basis or worksheet.
Inputs and outputs use existing Contract prose only when explicit authority
supplies them; only the selected Task, Review Tier, and lane/order results use
other existing fields. In-scope discovery, test-driven cross-module failure,
and unrequested work remain governed by current rules and cannot invoke this
policy.

````

## Captured Section 6: Stored Task Read And Privacy Contract

Source path: `docs/specification.md`

Source range: `## Stored Task Read And Privacy Contract` through immediately before `## Stored Contract Pointer Integrity Contract`.

````markdown
## Stored Task Read And Privacy Contract

Every Task-loading operation reads the source-schema capability once and
validates each complete loaded Task row through one shared row/batch validator
before public allow-listing, compact-field omission, filtering, derived-state
use, or use as a write basis. The validator does not normalize, coerce,
truncate, repair, or rewrite stored values.

For supported schemas through v22, exact text and nullable-text storage classes, exact SQLite
integers, stable IDs/project ownership, canonical lane/order, closed
kind/priority/status/review-tier enums, canonical timestamps, bounded
free-form privacy, and the blocker, pause, completion, current-review-target,
Contract-pointer, and completion-history cross-field matrices are validated as
one row contract. Text privacy is checked before its capacity. The validator
uses source-schema capabilities rather than per-row schema introspection and
accepts only columns valid for that supported source.
Task-row fetches share the same boundary: malformed or undecodable SQLite TEXT
and other non-busy fetch/decode faults use the fixed stored-state error before
projection, while genuine SQLite busy/locked state retains `database_busy`.

Bounded `task list`, `task current`, and `task next` operations select complete
Task rows and validate only that selected batch before filtering or projection;
they add no unrelated whole-table rescan. `task show`, Review Packet basis,
checkpoint, handoff, Effort Advisory, completion/review/verification lifecycle,
and metadata-only writes validate their selected Task row before dependent
content or mutation. Doctor, Viewer capture, setup, migration/reentry, and
managed recovery load every Task row without a project filter and validate the
complete batch, including project ownership. A caller that
already holds a validated Task row passes it to dependent review/history
readers, avoiding per-Task schema introspection or duplicate Task reads.

A current stored Task fault always fails closed with exit 2, code
`project_state_unreadable`, and message
`project state could not be read safely`. A normal command returns its existing
command-specific empty data shape, no warning, no partial projection, no
rejected bytes, and no write. Doctor uses the component mapping defined above.
A routine post-commit Viewer refresh preserves the committed business result
and last-good Viewer and emits only the existing fixed
`viewer_refresh_failed` warning; setup preflight fails no-write with the fixed
stored-state error, while a failure confined to setup's later Viewer stage
remains `setup_incomplete`.

Managed recovery preserves exactly one candidate-local exception: only stored Task
`verification` privacy or source-schema capacity failure is candidate-local.
Wrong storage class, enum, cross-field matrix, another Task field's
privacy/capacity fault, or any other structural Task fault is whole-set fatal
as `project_state_unreadable`; it cannot publish a canonical database or
select an older candidate.

````

## Captured Section 7: Stored Contract Pointer Integrity Contract

Source path: `docs/specification.md`

Source range: `## Stored Contract Pointer Integrity Contract` through immediately before `## SQLite, Migration, And Concurrency`.

````markdown
## Stored Contract Pointer Integrity Contract

After the scalar stored-Task row checks pass, the same shared validation boundary performs
exactly one bounded bulk relationship read for the loaded Task IDs when the
source schema is v8 or later and the batch is nonempty. Source schemas v1-v7
have no Contract capability and perform no relationship read.

For each loaded Task, `current_contract_revision=0` requires no related
`task_contract_revisions` row. A positive pointer requires every related row
to have exact TEXT same-project/same-Task ownership and an exact positive
SQLite INTEGER revision; the pointed revision must exist and equal the latest
raw related revision. Dangling, foreign-project, nonlatest,
revision-zero-with-row, duplicate, wrong-storage-class, malformed, or
ownership-mismatched relationship state fails before Task projection,
dependent-state use, or write. Values are not coerced through `int(...)` or a
SQLite aggregate before their storage classes are checked.

The relationship read is scoped only to the already validated selected batch.
It does not query once per Task, scan an unselected Task's Contract history, or
perform a general cross-table audit. Existing Contract-content validation
remains owned by the Contract repository after this relationship boundary.
Bounded list/current/next therefore retain their selected-row behavior, while
Doctor, Viewer, setup, migration/reentry, and managed recovery apply the check
to their existing whole-Task batch.

Every relationship fault uses the stored-Task validator's fixed exit-2
`project_state_unreadable` / `project state could not be read safely` result,
with no rejected bytes, warning, partial projection, or write. It is a
structural whole-set failure during recovery and never receives the candidate-local
verification privacy/capacity candidate-local exception. Valid revision-zero
and latest-positive states remain byte-compatible. Intentional empty review
target with positive generation and canonical stored-lane behavior are
unchanged.

````

## Captured Section 8: Task State And Selection

Source path: `docs/design.md`

Source range: `## Task State And Selection` through immediately before `## Completion Evidence And Review`.

````markdown
## Task State And Selection

### Task Model And Events

Tasks have stable random `tg_task_...` IDs, project ownership, bounded title
and description, kind (`sequential` or `optional`), lane/order, priority
(`low`, `normal`, `high`, `urgent`), status, blocker/pause reasons, review tier,
verification text, tags, timestamps, completion evidence, current review
target, current Contract pointer, Effort activity, and completion-history
coverage. IDs never encode a path.

Statuses are:

```text
ready
in_progress
paused
blocked
review_pending
done
cancelled
```

Blocked requires a reason. Paused requires a pause reason exactly while paused
and may be entered only from in-progress or review-pending; its normal exit is
in-progress and clears the current reason. Initial paused and initial done are
forbidden. `completed_at` is set only on done and cleared on reopen.

Task events are append-only concise audit summaries with stable random IDs.
The internal completion-cycle link added in schema v15 is never in
`PUBLIC_EVENT_FIELDS`; all event-return paths construct the six-field
allow-list explicitly. Latest-event ordering is
`created_at DESC, rowid DESC`. Tool events remain bounded operational records,
not raw logs.

### Shared Stored Task Row/Batch Validator

`stored_task_validation.py` owns one source-schema-aware validator for complete stored Task
rows. Raw fetches, scalar/relationship composition, current authority checks,
and the single-Task snapshot helper stay together in that repository-facing
module. Task operations, row projection, and Viewer proof consumption stay in
`tasks.py`; connection creation and proof issuance remain in `storage.py`.
Its capability object is constructed once per top-level read from the
already-observed source schema and describes the verification limit (500
through v17, 1,000 at v18) and the
presence of review-target base, Contract pointer, and completion-history
coverage fields. The validator receives rows and expected project identity; it
does not query schema metadata, mutate a row, or issue a database write.

Exact text/nullable-text and SQLite integer storage classes are checked before
any value helper can trim or coerce. The validator then applies field privacy
and capacity, stable identity, enum, canonical lane/order/timestamp, and Task
cross-field rules. It raises only `StorageError("project_state_unreadable",
"project state could not be read safely")`. Current Task converters remain
pure allow-list builders and are invoked only after their complete input batch
passes.
The public `task add` and explicit `task edit --verification` ingress is
independently capped at 1,000; every stored/read/internal path uses the source-
schema limit and never revalidates untouched bytes as new caller input.
The shared Task fetch helpers map non-busy SQLite query or UTF-8 decode failure
to that fixed error before a row reaches the validator, while preserving the
existing `database_busy` result for actual busy/locked state.

`list_tasks`, `list_current_tasks`, and `select_next_tasks` select complete
rows with their existing SQL bounds and validate that selected batch before
tag filtering, conversion, or compact omission; they never add a whole-project
validation query. Single-Task reads and lifecycle/write bases route through
the same validator. Whole-project consumers—doctor and Viewer—and setup or
recovery validation load every Task row without a project filter and pass that
complete ownership-checked batch. Managed recovery
sets its explicit verification-local flag so only that field's privacy or
source-capacity failure remains candidate-local; every other Task fault is
structural and set-fatal.

Viewer supplies the source version returned by snapshot validation. For exact
schema v18-v22, that validation completes the full Evidence Ledger and Task batch
checks before issuing one private, one-shot batch proof bound to the same
query-only connection and transaction, project, source version, exact sorted
Task IDs/count, issuance data version, and a fixed nested savepoint held only
in a module-private exact-object issuance registry. The private Viewer ordering
path revokes and consumes that proof once; mismatch, reconstruction, reuse, or
a changed
transaction fails closed. Other callers and source schemas v5-v17 retain the
ordinary complete Task validator. Review evidence consumes the already-
validated Task where available and derives column capability from that
version, removing the per-Task `PRAGMA` path. Routine Viewer failure occurs
before rendering/replacement and preserves the last-good file; its caller
applies the existing fixed maintenance warning.

### Stored Contract Pointer Relationship Boundary

For source schema v8 and later, `validate_stored_task_rows` receives the active
SQLite connection in addition to the already loaded complete Task batch. Only
after all stored-Task scalar checks pass, `stored_task_validation.py` extracts the exact selected
Task IDs and performs one `task_contract_revisions` query whose predicate is a
single JSON-encoded ID set consumed by `json_each`. The query intentionally
does not filter `project_id`, so a foreign owner using a selected Task ID is
observable as corruption. An empty batch and source schemas v1-v7 issue no
relationship query.

The relation predicate derives both the exact TEXT key and its UTF-8 BLOB form
from each selected Task ID. The BLOB form exists only to surface a
wrong-storage-class alias to the raw validator; it neither admits an unrelated
Task ID nor changes the selected-batch boundary into a general table audit.

The relationship reader returns raw `project_id`, `task_id`, and `revision`
values for only those selected IDs. Python validates exact TEXT identity and
positive SQLite INTEGER revision before calculating the latest revision; it
does not use `MAX(revision)` or `int(...)` before storage-class validation.
Revision zero requires no returned row. A positive pointer requires a matching
row, same-project/same-Task ownership for every returned row, and equality to
the raw latest revision. Duplicate, dangling, foreign, nonlatest,
revision-zero-with-row, decode, storage-class, and ownership faults all raise
the existing fixed stored-state `StorageError`.

The composed validator is called once by add post-read, list/current/next,
show, Viewer, `read_task`, `read_internal_task`, locked write-basis reads,
setup/doctor preflight, migration/reentry, and recovery. Review Packet,
checkpoint, handoff, Effort, and evidence/completion lifecycle paths inherit it
through their existing Task reader. No consumer owns a second relationship
rule or per-Task query. `contracts.py::read_current_contract` retains Contract
content validation after this boundary; unrelated Contract history is not a
general audit target. Recovery's verification-local result is evaluated only
after relationship validation, so every relationship fault remains structural
and set-fatal.

The shared single-Task fetch helper opens one short read transaction only when
its caller has not already established a transaction. The Task row and its
relationship rows therefore come from one SQLite snapshot even when another
writer commits a Contract revision between calls. Existing query-only and
locked write transactions are reused unchanged; the helper performs no write
and closes only the read transaction it owns.

### Sequential Ordering

One repository predicate determines whether an earlier same-project,
same-lane sequential row is incomplete. Only done and cancelled predecessors
are complete. `task next` and direct transitions to in-progress,
review-pending, or done use that predicate. Task add and edits that change
kind/lane/order/status validate every already-active, review-pending, and done
row in both affected lanes inside the serialized write, preventing
registration or reordering ahead of active successors. There is no override.

Lane input is trimmed and validated once. Sequential omission chooses the
deterministic default lane and next order; all integers fit SQLite signed
64-bit and next-order overflow fails before addition. The unique
project/lane/order index enforces final storage uniqueness.

Next-task order is priority (`urgent`, `high`, `normal`, `low`), lane,
lane-order with nulls last, creation time, then task ID. Default limit is 5.
Paused, blocked, active, review-pending, done, and cancelled rows are not next
candidates. A positive paused population adds one fixed count-only advisory
without changing candidate data or exit status.

`task list` uses the same priority, canonical-lane, nulls-last lane-order,
creation-time, and task-ID order. Its default limit is 20 and maximum is 100.

`task current` selects in-progress, review-pending, paused, and blocked rows,
optionally one valid status, with default limit 20 and maximum 100. It reuses
the latest event/checkpoint and deterministic fixed next-action mapping. It
does not calculate staleness or write a checkpoint. List/current/next remain
bounded and have no pagination cursor.

### Done Immutability And Reopen

Every task/review mutation loads the owner and applies the shared done guard.
A done Task accepts only the exact reopen edit:

- resulting status `in_progress`;
- one non-empty sanitized reopen reason; and
- no other task, note, completion, review, or Contract input.

The reopen writer checks review-generation overflow and sequential ordering,
clears current completion evidence and review target/base, advances review
generation, clears completion time and hold reasons, and appends
`task_reopened`. It preserves prior events, receipts, findings, Contract
revisions, and completion cycles. Schema-v16 reopen additionally validates and
links the latest saved cycle as described below. All other done writes return
`done_task_requires_reopen`.

A review-tier increase is a normal edit. A decrease needs one sanitized reason
and is permitted only while ready, in-progress, paused, or blocked, before any
review target ever existed: generation 0 and empty kind/value/base. It cannot
share completion input or a transition to review-pending/done. Generation
greater than zero permanently proves structured review started.

````

## Captured Section 9: Task Contracts, Checkpoints, Handoffs, And Effort

Source path: `docs/design.md`

Source range: `## Task Contracts, Checkpoints, Handoffs, And Effort` through immediately before `## Approved TG-M16 Reduced Loop Discipline Trial Design`.

````markdown
## Task Contracts, Checkpoints, Handoffs, And Effort

### Immutable Task Contract Revisions

`contract_content.py` validates supplied values or rows without database I/O,
using the shared value/error helpers. `contracts.py` retains revision-zero
projection, current/latest queries, activation, allocation, replay, and
transaction/event coordination; `storage.py` calls the same content validator
for rows it selects. Content validation does not own DB-backed Task/Contract
pointer integrity.

Schema v8 gives Tasks a current revision pointer and adds append-only
`task_contract_revisions`. Revision 0 has no row and projects empty fields.
Each positive row stores normalized scope, acceptance, optional constraints,
stable authority reference, change reason, and timestamp. Repository reads
require the pointer to reference the latest same-project/same-task revision;
the shared Contract-pointer boundary enforces this before Contract projection or
Task-backed lifecycle use.
Scope and acceptance are each capped at 4,000 characters, constraints at
2,000, authority reference at 500, and change reason at 1,000.

Supplying any Contract option supplies the group and requires scope and
acceptance. Initial Contract recording is allowed:

- on Task add only for ready, in-progress, blocked, or review-pending; or
- for an existing revision-0 Task only in the exact ready/blocked to
  in-progress activation, with empty completion/review state and no companion
  mutation.

Initial change reason is empty. Later revisions are Contract-only edits while
ready, in-progress, paused, blocked, or review-pending and require a semantic
scope/acceptance/constraints change, bounded non-empty reason, and stable
authority reference. Done must reopen and cancelled rejects Contract input.
Authority can name a revisioned governing file/decision or exact
`user_instruction:<task-id>:<next-revision>`; raw prompt text and current Task
output are not authority.

Normalization converts CRLF/CR to LF and strips outer whitespace while
preserving internal text. Omitted later constraints retain the current value;
explicit empty removes them. Exact semantic replay returns the current
revision without write even if authority/reason labels differ. A real change
allocates the next revision under the writer, updates the pointer, resets all
completion evidence, clears/advances any started review target, moves
review-pending to in-progress, updates time, and appends a content-free
`contract_revised` event atomically. Old Contracts and review history remain.

Stored Contract projection has one narrow legacy M19.7 seam: only
`constraints_text` is validated through the bounded legacy reader and returned
unchanged. Normal Contract input never selects that reader. When later
constraints are omitted, the established carry-forward rule may copy those
already-validated bytes into the new immutable revision; this preserves
lineage and supplies neither caller-input acceptance nor authority.

Concurrent identical input records once and replays; different valid input
serializes into successive revisions. Current-or-next user-instruction
placeholders are rebound to the locked allocation. A lost-response retry with
the older placeholder may replay; an unrelated placeholder cannot authorize a
new semantic change.

### Typed Checkpoints

`task checkpoint` requires bounded summary and next action and accepts at most
eight bounded unresolved risks. Limits are 1,024 UTF-8 bytes for summary and
next action, 512 per risk, 4,096 aggregate risks, and 6,144 for caller input.
One append-only row stores those fields, Task/project, current Contract
revision, and time. The same transaction adds fixed event
`checkpoint_recorded` / `Checkpoint recorded` without content and does not
change `tasks.updated_at`.

Exact replay of the latest same-Contract checkpoint is no-write. Done Tasks
reject it. Current/show expose only the latest checkpoint; Viewer excludes
checkpoint content. A checkpoint is optional and changes no Task status,
scope, selection, review, evidence, or completion gate.

Only stored checkpoint `summary` projection may use the bounded M19.7 reader
for the former numeric `dispatch_authorization` JSON field. It returns the
original canonical stored summary and writes nothing. New summaries and all
other checkpoint fields use the ordinary privacy path.

### Local Handoff Outbox

Schema v7 owns `handoff_records` with source Task/current Contract revision,
canonical idempotency key, optional explicit occurrence ID, bounded
summary/rationale, state, adapter/delivery metadata, internal claim lease,
bounded receiver receipt, and withdrawal data. Public states are:

```text
pending_handoff
handed_off
handoff_withdrawn_by_user
```

The state matrix requires pending without terminal timestamps, handed-off with
acknowledgement time and no withdrawal, and withdrawn with user reason/time,
no receiver receipt, and zero delivery attempts. Claim tokens are never
public. Every stored free-form field and matrix is revalidated before output;
corrupt/private stored content returns a fixed internal error rather than
redaction.

Canonical compact JSON over project, source Task, source Contract revision,
normalized summary/rationale, and occurrence ID is SHA-256 hashed as the
unique idempotency key. Omission canonicalizes to empty; an explicit occurrence
must come from explicit user instruction or deterministic external identity.
Summary and rationale are each capped at 1,000 characters and occurrence ID at
200; an explicitly empty or invalid occurrence is rejected rather than treated
as omission.
Exact replay returns the row and writes nothing. The local transaction commits
before any possible delivery; success reports durable only after commit.
Handoff never changes source Task state, acceptance, timestamps, events, or
selection.

List defaults to pending, oldest-first, limit 20/max 100, with exact
`total_matching` and rows in one snapshot. Terminal states require explicit
filter. Show uses the full public allow-list, while Task show exposes only
per-state counts. Withdraw requires pending, zero attempts, no claim ever, and
a sanitized user reason; it is a single immediate transaction.

The shipped product has no receiver and no public sync command.
`adapter_enabled=false`; pending records remain durable and rediscoverable.
The schema reserves a local versioned idempotent claim/delivery state machine,
but a concrete Issue adapter remains blocked until a separately approved
local intake contract exists. Task Skill never performs Issue triage,
priority, lifecycle, network/GitHub access, arbitrary code loading, shell
execution, or Issue-database access.

Any later sink must use stable `handoff_id` as receiver idempotency key and
return only accepted/retryable/permanent plus a bounded receipt. Claims use
compare-and-swap and expiry; an ever-claimed row is never withdrawable.
Retry stages are fixed 60-second then 300-second waits, then exhausted, with
permanent error terminal for delivery attempts. This reserved state does not
add a normal Task-loop call until a real adapter is approved.

### Optional Effort Advisory

`effort.py` reads only strict optional
`<skill>/config/effort-advisory.json`. Version 1 accepts exactly profile
`informational-v1`, explicit enabled, and thresholds for five fixed metrics.
Missing or valid disabled configuration is off; invalid present configuration
is disabled with a bounded continuation diagnostic. There is no generic
configuration store, inheritance, environment override, writer, or configured
command runner.

Thresholds are nonnegative JSON integers for only changed files, changed
lines, changed modules, Contract revisions, and handoffs, and exceed only when
the measurement is strictly greater. An invalid profile uses the fixed
continuation warning; a threshold result uses at most one warning with code
`effort_advisory_threshold_exceeded`, key
`effort_advisory.threshold_exceeded.v1`, and fixed non-content message.

When enabled, the first transition to in-progress may best-effort capture
canonical Git basis, cleanliness, timestamp, project/subject activity
generations, and whether another task was active. Git observation is
argument-vector, optional-lock-disabled, no-lazy-fetch, fsmonitor-disabled,
submodule-ignored, no-external-diff/text-conversion, and read-only. Capture
failure stores no partial basis and never blocks start.

Metrics are changed files, changed lines, modules, Contract revision count,
and source-task handoff count. Attribution is unknown for non-Git, dirty or
uncertain endpoints, incomplete line coverage, or activity evidence of
overlap. Untracked/binary content makes line coverage unknown instead of
guessed. With a basis, pre/post DB observations are separately coherent around
Git; the post-read refresh detects overlap without keeping a transaction open.
Strict-off databases do no advisory bookkeeping.

The advisory never writes acknowledgement, asks a question, chooses a
handoff, changes status, expands scope/acceptance, or blocks completion.
`task show` mechanically exposes enablement. The Skill calls `task effort`
once at the existing verification/review boundary only when enabled.

````

## Captured Section 10: Approved TG-M16 Reduced Loop Discipline Trial Design

Source path: `docs/design.md`

Source range: `## Approved TG-M16 Reduced Loop Discipline Trial Design` through immediately before `## Setup, Doctor, Backup, And Maintenance`.

````markdown
## Approved TG-M16 Reduced Loop Discipline Trial Design

The existing Effort result chooses:

```text
valid enabled profile AND a nonempty ordered exceeded list
  => suggested_action=reconcile_scope
otherwise
  => suggested_action=continue
```

The same value appears in the one existing threshold warning. Attribution and
unknown reasons remain evidence only: unknown without an exceeded threshold
continues; an exceeded threshold with unknown attribution reconciles. This
adds no metric, profile field, database write, stored acknowledgement, routing
framework, or Task/handoff/review operation.

Reconciliation is session-local guidance, not persisted state.
`references/reconciliation.md` is loaded only after `reconcile_scope` or a
repeated test/review failure. After two materially equivalent failed repairs,
a third equivalent execution is prohibited without new evidence. Renaming a
wrapper, command, directory, Task, or execution unit is not new evidence; a
diagnostic is new only when its result can materially change the causal
hypothesis, authorized repair, or expected result. A fresh session resets the
comparison and relies on durable Task/event/review/handoff state.

Tests are never weakened merely to obtain PASS. A failing test or Effort
signal is evidence, not authority. A test may change only when current
authority establishes it is wrong; a Task Contract or acceptance change still
needs later explicit authority.

Scope reconciliation reuses the existing three-way classifier: work stays in
the current Task only when accepted scope and current authority cover it;
other discoveries go to local handoff; a blocker is used only after safe
authorized repair for the affected Task/lane is exhausted. Paused remains an
explicit temporary interruption, and unrelated ready lanes continue.
Remaining user decisions are batched.

Review remediation starts from the current blocking receipt/finding. A
meaningful fix sets a fresh target and obtains a fresh current-generation
result. A result that remains blocking counts as one unsuccessful cycle;
completion still requires fresh qualifying PASS receipts. After two
materially equivalent unsuccessful cycles without new evidence, no third
equivalent cycle runs; the same bounded blocker/decision path applies.

No M16 setup stage, Task seed, policy version, instruction-chain inspection,
target `AGENTS.md` mutation, persisted counter, alternate state machine, or
normal-loop call exists.

````

## Captured Section 11: Current M25 Select-Split-Merge-Register Design

Source path: `docs/design.md`

Source range: `## Current M25 Select-Split-Merge-Register Design` through immediately before `<a id="trusted-local-runner-architecture"></a>`.

````markdown
## Current M25 Select-Split-Merge-Register Design

M25.1, Task `tg_task_8e33e15cd97a28ee`, froze the instruction-layer design in
`docs/specification.md`. M25.2, Task `tg_task_d891cd538d9e7364`, activates it
only in Skill guidance and the task-workflow package reference. The
deterministic CLI, repository interfaces, schema, Viewer, Runner, and command
inventory remain unchanged.

### Session-Local Select-Split-Merge Classifier

The Skill treats one semantic taskization or scope-addition outcome as
one session-local event and classifies it as either `registration` or
`mid_task_scope_addition`. Replies, clarifications, paraphrases, and answers
about that outcome remain the same event; only materially changed authority for
scope, order, or permission creates a new event. An in-scope discovery, test
failure, Effort result, cross-module failure, inferred dependency, task size,
or model preference does not create either event.

For the event, session-local reasoning holds only:

- the complete authorized outcome and unchanged permission boundary;
- explicit Contract facts, binding Review Tier mappings, order, and placement;
- flat candidate responsibilities with their consumed inputs, produced outputs,
  repository-state boundary, local verification/review attribution, and
  existing lane/order representation; and
- concrete fragment-to-owner coupling used by the one global Merge.

These values are not a database record, JSON contract, Task field, dependency
model, parser input, helper-owned worksheet, or prompt-log artifact. A fresh
agent boundary is satisfied when the final group's existing Task Contract,
routed authority, and declared predecessor outputs are sufficient; it has no
numeric token, file, line, or duration threshold.

The classifier runs this fixed sequence once:

1. `Select` the one explicit authority envelope and stable responsibility
   boundaries within it.
2. `Split` once into a flat candidate set whose exact unions conserve the whole
   outcome and explicit permission envelope without omission or expansion, and
   whose sequence uses representable order. A provisional candidate may expose
   missing repository-state or attributable-gate independence so that the Merge
   can attach that fragment to its concrete owner.
3. `Merge` all concretely coupled, fragment-only transitive groups in one global
   pass. Multiple disjoint groups may merge simultaneously; sharing files,
   tests, commands, or fixtures alone creates no coupling. Ambiguous ownership
   invokes the fallback rather than an arbitrary group.
4. Treat the resulting groups as final. There is no recursive classification,
   second Merge, re-Split, parent/child graph, or size-based optimization.

Only final groups must each own one bounded responsibility and its authorized
inputs/outputs, leave a correct repository state after represented predecessors,
carry locally attributable verification and review, and be resumable from
Contract, routed authority, and declared predecessor outputs.

### Registration Adapter And Partial-Add Recovery

For explicit taskization, each final group uses one existing `task add`. The
group's non-zero Contract copies only explicit scope, acceptance, constraints,
and authority reference. Inputs and outputs remain prose in those existing
fields, while order remains existing lane/order; no dependency or worksheet
field is introduced.

If outcome and registration permission are clear but split or Contract detail
is missing, the adapter adds one whole-outcome Task with Contract revision zero.
It asks no per-candidate question. It asks one grouped question, with no partial
write, only when exact user-mandated boundaries conflict with a truthful final
set or outcome/permission is too unclear for one honest whole-outcome Task.

If explicit authority requires an implementation-binding design decision first,
the adapter registers only that design responsibility unless the same authority
already states truthful ordered design and implementation Contracts. A later
implementation Task requires a later explicit registration event based on the
produced design authority; the adapter does not synthesize it.

After a strict subset of an existing `task add` sequence succeeds, the adapter
stops and reads the exact registered set. During the same uninterrupted event,
it compares that read with the still-transient final set, preserves successful
additions and the authorized remainder, then adds only groups proven missing
after the ordinary failure is resolved. It performs no deletion, duplicate add,
repartition, batch retry, or second Select-Split-Merge pass.

If interruption loses the transient final set, the adapter does not reconstruct
or rerun it. It uses existing Handoff only for a truthful bounded remainder
summary, never as a hidden worksheet or registration authority. Any later write
requires current explicit authority; unclear remainder or permission uses the
one grouped question, and a confirmed unsplit remainder may be one revision-zero
Task without changing successful additions.

Registration changes governance state only. It grants no implementation,
target-project, Git, network, or external-system permission.

### Review Tier Resolver

The instruction layer resolves each final group before registration:

1. apply every explicit binding authority floor governing that scope;
2. with no binding mapping, select Tier 2 for schema/migration, JSON contract,
   CLI write behavior, target mutation, privacy/logging, Skill trigger,
   verification/review/completion gate, milestone/plan acceptance, or
   implementation-binding normative documentation;
3. otherwise select Tier 0 only for wholly mechanical meaning-preserving work;
   and
4. select Tier 1 for every remaining scope.

An explicitly authorized higher Tier is permitted. Ordinary registration input
cannot lower a binding floor; only an explicit governance change can change the
mapping. Unknown, size, difficulty, duration, failure count, safety wording, or
reviewer availability never alone selects Tier 2. Each Task retains its own
review gate; later integration review is not a substitute.

### Mid-Task Adapter And State Effects

For an explicit addition to an `in_progress` or `review_pending` Task, the
Skill applies the same one-pass classifier to the addition and its
relationship to the current responsibility:

1. already-covered scope selects `keep-current`, performs no Contract write,
   and preserves the current Tier;
2. a final group kept in or globally merged into current computes
   `max(current Tier, resulting floors)`. When higher, the existing Tier edit
   occurs first; the semantic Contract-revision write follows. It never
   auto-lowers and review does not advance between those writes. A failed second
   write therefore leaves unchanged scope at a conservative higher Tier;
3. a successor uses its own scope floor and existing lane/order. The same
   message registers it only when explicit taskization or separate placement is
   present; otherwise the adapter emits one write-free proposal; and
4. an addition that cannot yet be placed truthfully uses existing bounded
   Handoff, then continue, pause, or block only under their existing conditions.

Moving already-covered work requires explicit repartition authority, and an
unrepresentable order never creates an implicit dependency. Existing semantic
revision continues to invalidate stale target, review, and completion evidence;
`keep-current` preserves them only under ordinary exact-target rules. A pending
proposal is not persisted as a new model or reconstructed from Handoff. The
same event cannot run another Split after global Merge. Scope preservation
precedes any pause or block, and the normal Task loop gains no new call.

### Atomic Instruction-Layer Synchronization Boundary

M25.2 changes these surfaces in one reviewed Tier 2 revision:

- add only the concise trigger gate and disposition rules to
  `task-governance-tool/SKILL.md`;
- place the full one-pass sequence, responsibility/fragment cases, Tier table,
  ordering examples, and recovery rules in
  `task-governance-tool/references/task_workflow.md`;
- update `task-governance-tool/release-manifest.json` only for the changed
  package digests under the current version/release rules;
- switch the formerly inactive markers and implementation-facing routing in
  `docs/specification.md` and `docs/design.md`, and synchronize the approved
  static execution contract in `plan.md`; and
- update `tests/test_skill_self_containment.py`,
  `tests/test_m14_integrated_acceptance.py`, and
  `tests/test_document_history.py`, adding a focused test module only if those
  owning suites cannot express the behavioral cases without duplication.

`task-governance-tool/agents/openai.yaml` is in the synchronization review set
and remains byte-identical because its current registration and
scope-preservation metadata already covers the two triggers. Scripts,
migrations, repositories, CLI parsing/output, Viewer code/template, and public
command leaves are outside the write set and must be proven unchanged.

### Neutral Forward-Test Boundary

M25.2 acceptance uses fresh, minimal-context agents that receive the
candidate Skill and neutral workloads, not the expected branch or M20S study
result. A separate evaluator checks both the response and resulting Task DB.
The fixed matrix includes:

- responsibility slices that share files, tests, commands, or fixtures yet
  remain separate; an internal enabling slice without standalone user value;
  and fragment ownership whose transitive groups merge once with no re-Split;
- exact scope/permission conservation, representable ordering, fresh-agent
  reconstruction from Contract/authority/predecessor output, and ambiguous
  ownership falling back instead of being guessed;
- one whole-outcome revision-zero Task, the sole grouped-question boundary,
  design-first without automatic implementation registration, same-event
  partial-add recovery that preserves successful writes and adds only proven
  omissions, and interruption after a partial add with no reconstruction;
- binding Tier floors, one example for every Tier 2 protected category, wholly
  mechanical Tier 0, residual Tier 1, every excluded Tier-2 rationale, explicit
  higher Tier, and no ordinary lowering of a binding floor;
- mid-Task keep, revise, successor, and merge-into-current Tier effects,
  including separate Contract/Tier writes and no auto-lowering; and
- unchanged current command leaves, schema, Runner, normal-loop call count,
  target/evidence freshness, Handoff bounds, and no invocation from discovery,
  test failure, Effort, or cross-module failure alone. Each Task's review gate
  must pass independently of later integration review.

Positive, negative, and unknown cases use parallel wording and equal available
authority so the prompt does not reveal the expected result. A valid result
must match the specification branch and actual stored effects; self-reported
intent alone is insufficient.

````
