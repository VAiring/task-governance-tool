# Task Operation Specification

This document owns Task selection, state, Contract, Checkpoint, local Handoff,
Effort Advisory, and instruction-layer operation delegated by the
[product specification](specification.md#task-state-scope-review-and-completion).
Implementation structure belongs in the [Task operation design](task-operation-design.md).
The shared [CLI/output](specification.md#public-cli-and-output-contract),
[privacy and stable errors](specification.md#privacy-safety-and-stable-errors),
[review and completion](review-completion-specification.md),
[completion history](review-completion-specification.md#completion-cycle-history), and
[SQLite operation](specification.md#sqlite-migration-and-concurrency) contracts
remain with their existing owners. Evidence and Runner detail retain their
separate current owners routed by the [authority index](authority.md).

## Task Selection And Read Commands

<a id="task-selection-and-read-commands"></a>

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

`task show` defaults to one fixed working-context projection. It retains the
complete Task and current Contract, latest checkpoint, handoff counts, current
review/verification gates and operational evidence, completion-history counts,
and suggested action. Repeated target, tier, and verification text are emitted
once at their Task/Contract owner, rather than again in evidence summaries.
The [Review/completion owner](review-completion-specification.md) defines the
working evidence and explicit audit projections.

`task show --audit` explicitly obtains the previous bounded detail projection,
including recent Receipt/provenance and Finding rows and saved completion
cycles. It is for history investigation, not another normal-loop read, a
free-form field selector, an exhaustive export, or a current completion basis.
Normal use requires no mode choice, remembered reads, or follow-up detail query.
Both modes perform all existing selected-Task, evidence, history, privacy, and
Runner validation before presentation; hiding a detail never bypasses a check
or changes a gate, stored row, Evidence artifact, or Viewer projection.

Normal events retain the newest event and every `note_added`, `task_updated`,
`review_tier_changed`, and `task_reopened` event, without duplication, ordered by
`created_at DESC, rowid DESC`. These types can carry caller notes, transition
reasons, or reopen context. They have no age/generation cutoff: neither a newer
mechanical event nor a checkpoint is proof that an older note is superseded.
No event prose is parsed to infer relevance. The retained subset can therefore
grow with genuine operation history. Audit events keep the previous newest-ten
window; normal events are not derived by filtering that window.
Additional event summaries exposed beyond that former window must satisfy the
existing event-summary text/privacy limits before output. Their original bytes
are returned unchanged; rejection uses the existing sanitized
`project_state_unreadable` failure without a partial result or write. This local
output check does not change audit reads or global state admission.

The read is query-only. Both modes return exactly one routing Boolean
`effort_advisory_enabled`; invalid advisory configuration returns false plus
the existing continuation warning. Human text remains concise and does not
replace the complete JSON Contract or gate information.

`task context` is the fixed read-only start/resume operation; it accepts only
the common CLI options. It uses the existing validated current batch (limit
20), resumes its first `in_progress` or `review_pending` row in existing
order, or otherwise uses the existing validated next batch (limit 5) and picks
its first candidate. Selection uses these bounded batches before display
omission, not the compact prefixes. Held rows remain recalled and do not
suppress unrelated ready work. It returns the selected Task's complete normal `task show` data, including
Contract, latest checkpoint, current constraints/blockers, gates, and routing.
It never starts a Task or changes state, evidence, or gate requirements.

Success data is exactly `selection`, `current`, `next`, and `selected`.
`selection` is `current`, `next`, or `none`; `current` is the existing compact
current data; `next` is the existing compact next data only when fallback ran,
otherwise null; `selected` is the complete normal show data or null when no candidate
exists. Each compact component is projected from the same batch used for
selection; an omitted display row may still be the selected Task. Component
ordering, row limits, and omission budgets remain unchanged. Successful
component warnings are retained once. Any read failure stops without another
candidate and preserves its sanitized code/exit status, with no warnings and
exact empty data `{selection: "none", current: null, next: null, selected: null}`.
Thus successful absence is distinct from failure through `ok`.

The public operation reuses the resolver's retained read for selection and
detail. Live marker-2 show retains its existing release, physical Runner-basis
selection, and Task comparison before final projection; there is no claim of a
single SQLite snapshot across that physical phase or a second global scan.
Individual current/next/show commands remain available for explicit inspection.

Every Task-loading operation applies the current stored-row and
Contract-relationship contracts
before an allow-list projection, compact omission, derived-state use, or
write-basis use. Bounded list/current/next reads validate the complete rows in
their selected batch and do not add an unrelated full-table scan. `task show`
and Task-backed lifecycle operations validate the selected complete row before
reading or mutating dependent state. The shared failure result is defined in
the [stored-Task validation contract](#stored-task-read-and-privacy-contract).

The deterministic Skill call graph is:

- one `task context` call to select or resume and read the complete current
  Contract, latest checkpoint, and Effort Advisory routing flag, without
  intermediate LLM branching or follow-up current/next/show calls;
- one task edit only when the selected Task is ready and implementation is
  authorized; an already active Task needs no redundant start write;
- only for a deterministically enabled Effort Advisory profile, one existing
  `task effort` observation at the verification/review boundary;
- one review target set call after the exact material is ready; its returned
  `verification_route` and `blocking_code` deterministically select the
  not-required, Receipt-required, qualifying Runner-pass, or blocking branch;
- only for `verification_route=receipt_required` on the marker-`0` manual branch
  or exact closed no-launch fallback, one `verification receipt add` call after
  the caller runs the complete governed verification against that exact target;
  the not-required and qualifying Runner-pass branches need no Receipt call;
- the Packet returned by qualifying Receipt registration, or one standalone
  `review prepare` on a Receiptless route, instead of separate Task, Contract,
  target, and Git context reads;
- one `review result add` to record the actual structured Receipts and Findings
  together for the packet's exact Task/Contract/target; and
- one thin complete call.

A default-off no-finding Tier 2 manual/fallback path therefore has at most
six governance subprocess calls; a profile-enabled path has at most seven.
The qualifying Runner-pass path uses standalone preparation instead of
Verification Receipt add and has the same bounds. The existing individual Receipt
path remains available and takes one additional call for two Receipts without
Findings. All counts exclude real progress updates, the external verification
process and the two independent review model decisions; fewer registration
calls are not a measurement of total LLM tokens.
For a new Task whose registration and immediate start are already authorized,
the [registration rule](#explicit-registration-and-contract-population) records
initial `in_progress` and retains the following context read. Only the separate
start edit disappears. With individual Review Receipts, the same new-registration
manual flow goes from eight calls to seven (including add); with grouped Review
Results it goes from seven to six. Already registered/active flows gain no
registration saving. These comparisons hold automatic Packet preparation fixed
on both sides; they isolate only the initial-start saving and do not measure
token savings.
`task complete --check`, `doctor`, and `task checkpoint` are absent from the
default success path.

## Task State, Scope, Review, And Completion

<a id="task-state-scope-review-and-completion"></a>

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

### Structured Task Registration

`task add --from-stdin` registers one already-approved, caller-finalized Task
set. It is an input mode of the existing leaf, not a splitter, authority
resolver, implementation-start permission, or new normal-loop step. Individual
Task/Contract options cannot accompany this mode; the combination is rejected
before state access. Single-Task flags, defaults, and result shape remain valid.

Stdin is one UTF-8 JSON object, at most 262,144 bytes, with exactly required
`version=1`, `common` (object), and `tasks` (array of 1 through 64 objects).
Unknown/duplicate keys, BOM, invalid UTF-8, non-finite/fractional numbers,
surrogates, and wrong JSON types are rejected without retaining input bytes.

Each item requires its own `title` and `contract`. `contract` is either `null`
(revision zero), or an object with required `scope` and `acceptance` and optional
`constraints` and `authority_ref`. Common Contract values are limited to those
last two optional fields and never activate a null Contract or supply scope or
acceptance. `contract_change_reason`, IDs, timestamps, and target/evidence fields
are not inputs. Initial Contract status/content/authority rules remain unchanged.

Common and per-item Task fields are `description`, `kind`, `lane`, `lane_order`,
`priority`, `status`, `blocked_reason`, `review_tier`, `verification`, and `tags`.
Only per-item input permits `title`. The optional `common.contract` is an
object containing only `constraints` and/or `authority_ref`. Per-item explicit
values override common values, including empty text; missing values otherwise
use existing single-add defaults. The LLM must explicitly select each effective
`review_tier`, individually or in `common`; the batch does not infer it.
Integers are JSON integers, not booleans or strings; `lane_order` also permits
null for the existing automatic-order behavior. Other scalar inputs are
strings and use existing privacy, bounds, normalization, and enum checks.
Common values are checked even when overridden or unused. Effective Task and
Contract combinations undergo the existing validators and sequential guards.

Registration is all-or-nothing in input order under one writer transaction,
including Task, Contract, authority snapshot, event, and Effort bookkeeping.
Success `data.tasks` contains one entry per input with zero-based `input_index`,
the existing `task` and `event`, and `contract_write` only when recorded.
Handled batch input/storage failures return `data.tasks=[]`; pre-dispatch parse
and read-only rejection retain the ordinary error envelope. No successful
subset from this invocation survives a rollback; previously registered Tasks
are never removed. Post-commit maintenance runs once for the whole mutation.

A confirmed rollback permits resubmitting the corrected explicit set. There is
no idempotency ledger or automatic retry: a lost response does not prove
rollback. Inspect the exact registered set through existing reads before any
further write and add only a remainder proven missing under current authority.
Never blindly replay, delete successful Tasks, or rerun splitting. Existing
partial-add recovery still applies to a sequence of separate single-add calls.

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
may therefore carry forward byte-identical constraints already validated by
the [stored legacy counter compatibility path](specification.md#privacy-safety-and-stable-errors)
as immutable lineage. This path preserves bounded positive canonical integer
`dispatch_authorization` counters in stored text; it does not validate
caller-supplied legacy vocabulary or grant new authority. Any explicitly
supplied constraints use the normal strict input guard.

A semantic revision appends immutable history, advances the pointer, clears
completion evidence, preserves generation 0 if review never began or otherwise
clears target and advances generation, moves review-pending to in-progress,
updates time, and appends `contract_revised` atomically. Fresh gates are
required. Done must reopen; cancelled rejects.

`task show` and `task context.selected` expose the full `contract` object: revision, scope,
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

## Reduced Loop Discipline

<a id="reduced-loop-discipline"></a>
<a id="approved-post-mvp-extension-tg-m16-reduced-loop-discipline-trial"></a>

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

## Typed Checkpoint

<a id="typed-checkpoint"></a>

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
`task show`, `task context.selected`, and default current expose only the latest object.

The stored-summary read path alone retains bounded compatibility for the
already-recorded numeric `dispatch_authorization` JSON counter and returns the
original summary unchanged, under the
[stored legacy counter privacy contract](specification.md#privacy-safety-and-stable-errors).
New checkpoint input uses the normal strict guard, and the compatibility read
neither records nor authorizes an external operation.

Command data is exactly `checkpoint`, `created`, `replayed`, and `event`.
Checkpoint keys are `checkpoint_id`, `task_id`, `contract_revision`,
`summary`, `next_action`, `unresolved_risks`, and `created_at`. New event
output contains only ID, type, and time; replay event is null. Text is
`Checkpoint <checkpoint_id>: recorded|replayed for task <task_id>\n`.

## Task Decomposition And Registration

<a id="task-decomposition-and-registration"></a>
<a id="current-m25-select-split-merge-register-contract"></a>

Task decomposition and registration apply to two and only two explicit
user-authority events: an instruction to register or taskize already-authorized
work, and an explicit scope addition to an `in_progress` or `review_pending`
Task. This contract is implemented only in current Skill and task-workflow
guidance. Discovery, a test failure, an Effort result, task size, or model
preference does not create either event.

### Candidate-First Split And One Global Merge

The Skill instruction layer first fixes one authority envelope containing the
complete authorized outcome, its permission boundary, any binding order, and
any explicit Contract or Review Tier mapping. It then performs one flat
candidate-first Split at responsibility boundaries with a concrete need or
benefit for separate completion. Such completion changes an actual approval,
use, or handoff decision, allows one outcome to finish while another is held,
or releases a foundation needed by already-authorized downstream work. A
provisional candidate states its bounded responsibility, authorized inputs and
outputs, and concrete coupling to other work. A candidate that cannot stand
alone is input to the one global Merge, not rejected before Merge.

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

A foundation need not deliver standalone user value, but an imagined future
consumer does not justify its separate completion. Independent implementation
or inspection, different files, modules, feature names, tests, or work phases
do not alone justify separate Tasks. Nor do file/line counts, estimated effort,
duration, or risk wording. Shared files, tests, commands, or fixtures alone
neither require nor prevent separate slices.

The flat candidates must conserve the authority envelope exactly: their scope
union is complete and non-overlapping, their permission union preserves the
complete explicitly authorized permission envelope without omission, no group
exceeds that original boundary, and their ordering adds no unapproved outcome.
The instruction layer then performs one global Merge pass over the complete
flat set. Merge fragments into their concrete consumer or acceptance owner,
and combine internal work on the same outcome when separate completion changes
no decision or use and mainly repeats verification, review, and completion
gates. A fragment cannot leave a correct repository state, carry attributable
local gates, or own the complete inseparable responsibility. Concretely coupled
work forms transitive groups in that same pass, which may merge several disjoint
groups at once. Do not cross explicit approval, order, or acceptance boundaries.
Keep separate responsibilities when combining them would make acceptance
conditions hard to map to the relevant changes and checks. Every resulting
group retains the gates required for its entire scope. Ambiguous ownership uses
the [registration fallback](#explicit-registration-and-contract-population)
rather than an arbitrary Merge.

The merged set is final for that explicit event. It is never Split again,
recursively decomposed, or optimized through a second Merge pass. Neither one
Task nor the fewest Tasks is a target. The criteria add no scorecard, token
estimate, split-reason output or record, or new confirmation step. If a valid
flat set cannot be formed, the registration and grouped-question fallbacks
in [registration](#explicit-registration-and-contract-population) apply instead of inventing another boundary. A reply, clarification,
paraphrase, or answer about the same taskization or scope-addition outcome stays
in the same event; only materially changed authority for scope, order, or
permission starts a new event.

### Explicit Registration And Contract Population

An explicit request to register or taskize work authorizes registration of
each final group through `task add`; multiple finalized groups use its
[structured input](#structured-task-registration) in one atomic registration.
It authorizes no implementation,
target-project mutation, Git or network operation, external delivery, or
permission expansion. Each non-zero Contract copies only scope, acceptance,
constraints, and authority reference that the governing sources or user
instruction state explicitly. It never infers acceptance or permissions merely
to make a split look complete.

When registration and immediate implementation are both already authorized,
the instruction layer uses the existing initial `in_progress` value for work
that can start under existing selection and predecessor order. It must not use
that value to bypass an existing active Task or an earlier ready candidate.
Otherwise it retains the appropriate initial state, normally ready. Registration
alone, successful `selection=none`, or candidate display omission supplies no
start permission or proof that no competing work exists. Paused/blocked work
alone does not prevent unrelated ready work. The subsequent read-only
`task context` remains necessary for selection, complete Contract, and current
gates; neither its selection nor initial status itself authorizes implementation.
This reuses the existing start decision at registration, without a new mode,
confirmation command, selection algorithm, or storage/Contract/evidence behavior.

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
clear, use the [grouped-question boundary](#explicit-registration-and-contract-population); a confirmed whole remainder may use
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
responsibilities cannot each meet the [final-slice conditions](#candidate-first-split-and-one-global-merge), the global Merge
keeps them together.

### Explicit Mid-Task Scope Addition

For one explicit addition to an `in_progress` or `review_pending` Task, first
preserve the scope and permission envelope, then apply the same completion-benefit
and Merge criteria once to the addition and its relationship to the current
responsibility:

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

Package explanation retrieval may replace whole-reference reads or line-range
searches with `scripts/read_reference.py "references/<file>.md#<section>"`.
The supported files are the shipped workflow, CLI contracts, and conditional
reconciliation references. The complete selected section and ancestor
introductions are returned as UTF-8 text without truncation; applicable linked
requirements still need reading. The caller chooses the existing link, not a
new operation taxonomy. Invalid/unavailable sections return failure with no
document body, never an unrelated whole reference. This is an optional document
read replacement, not a normal-loop addition, prerequisite, state operation,
permission grant, or change to the public taskgov command inventory. Local
retrieval measurements do not establish total LLM token or elapsed-time savings.

Select-Split-Merge decisions remain instruction-layer guidance in current
`SKILL.md` and `references/task_workflow.md`. The separately defined structured
registration mode only transports those explicit decisions. The guidance itself
adds no public command or normal Task-loop call and changes no
SQLite schema, JSON contract, Viewer field, automatic Task creation,
runtime Task splitting, parent/child or dependency model, background LLM work,
network behavior, or target-project mutation. Its grouping and tier-basis
reasoning remain session-local and are not persisted as a basis or worksheet.
Inputs and outputs use existing Contract prose only when explicit authority
supplies them; only the selected Task, Review Tier, and lane/order results use
other existing fields. In-scope discovery, test-driven cross-module failure,
and unrequested work remain governed by current rules and cannot invoke this
policy.

## Stored Task Read And Privacy Contract

<a id="stored-task-read-and-privacy-contract"></a>

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
rejected bytes, and no write. Doctor uses its [component mapping](setup-state-specification.md#doctor-contract).
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

## Stored Contract Pointer Integrity Contract

<a id="stored-contract-pointer-integrity-contract"></a>

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
