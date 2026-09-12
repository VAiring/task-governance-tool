# Task Operation Implementation Design

This document owns Task state/selection, stored Task and Contract relationships,
Contract revisions, Checkpoints, local Handoff, Effort Advisory, and
instruction-layer structure delegated by the
[implementation design](design.md#task-state-and-selection), for the behavior in
the [Task operation specification](task-operation-specification.md).
The shared [runtime ownership](design.md#runtime-module-boundaries),
[CLI/serialization](design.md#public-cli-and-serialization),
[connection/transaction](design.md#journal-and-connection-rules),
[completion/review](review-completion-design.md),
[completion history](review-completion-design.md#completion-cycle-history), and
[privacy/failure](design.md#privacy-safety-and-failure-boundaries) contracts
remain with their existing owner. Evidence and Runner detail retain their
separate owners routed by the [authority index](authority.md).

## Task State And Selection

<a id="task-state-and-selection"></a>

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
module. Task operations, shared row converters, and Viewer proof consumption stay
in `tasks.py`; `task_show_projection.py` assembles the Task detail result from
the caller's snapshot and existing validation/readers. Its latest-history
summary is only a text fallback, not an additional public JSON field.
That module also owns the fixed normal/audit presentation boundary. The audit
form preserves the previous bounded detail; normal presentation removes
redundant parent values and historical detail only after the same validation.
Show-only collectors reuse the validated Review and Verification streams for
operational Findings and exact-current Receipts, not their recent-ten windows.
The normal event query retains the newest event and all four note-capable
operation types defined by the Task read contract, with the existing timestamp/
rowid ordering. It does not infer supersession from age, checkpoints, or prose.
No storage writer, schema, global admission, gate evaluator, or Viewer reader
changes its contract because of this presentation choice.
Connection creation and proof issuance remain in `storage.py`.
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

`cli.py` shares current/next batch-read and result-projection helpers between
the individual command handlers and `handle_task_context`. The aggregate uses
fixed arguments and selects from the existing validated bounded batch, then
uses the same batch for compact presentation under its original command label.
It does not select from a display prefix or reread omitted candidates. Selection
defaults and compact budgets stay unchanged. It owns only the fixed aggregate
envelope, first-candidate routing, warning combination, and no-partial-result
failure projection, never a second selection predicate, Task validator, or
state writer. `cli_text.py` combines the selected show text with held-work
recall. Public state resolution retains one admitted read for the composition;
the existing live marker-2 show path still closes it before physical selection
and checks the observed Task on its final Task-local read. The aggregate does
not add global Runner validation or infer cross-phase snapshot atomicity.

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
links the latest saved cycle under [completion history](review-completion-design.md#completion-cycle-history). All other done writes return
`done_task_requires_reopen`.

A review-tier increase is a normal edit. A decrease needs one sanitized reason
and is permitted only while ready, in-progress, paused, or blocked, before any
review target ever existed: generation 0 and empty kind/value/base. It cannot
share completion input or a transition to review-pending/done. Generation
greater than zero permanently proves structured review started.

## Task Contracts, Checkpoints, Handoffs, And Effort

<a id="task-contracts-checkpoints-handoffs-and-effort"></a>

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

Stored Contract projection has one narrow compatibility path for legacy
operation counters: only `constraints_text` uses the bounded stored-text reader
for positive canonical integer `dispatch_authorization` counters and is returned unchanged.
The exact [privacy-only guard](design.md#privacy-safety-and-failure-boundaries)
retains all other detectors. Normal Contract input never selects that reader.
When later constraints are omitted, the established carry-forward rule may copy
those already-validated bytes into the new immutable revision; this preserves
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

Only stored checkpoint `summary` projection may use the bounded legacy
operation-counter reader for the former numeric `dispatch_authorization` JSON
field, under the shared [privacy-only guard](design.md#privacy-safety-and-failure-boundaries).
It returns the original canonical stored summary and writes nothing. New
summaries and all other checkpoint fields use the ordinary privacy path.

### Local Handoff Outbox

`cli_handoff.py` owns command handling for record/list/show/withdraw, including
the record transaction and its single fresh-transaction retry. Shared context,
state resolution, retained-reader cleanup, and post-commit coordination remain
in `cli.py`; `handoffs.py` retains the outbox domain operations below.

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
`task show` and its `task context.selected` projection mechanically expose
enablement. The Skill calls `task effort`
once at the existing verification/review boundary only when enabled.

## Reduced Loop Discipline Design

<a id="reduced-loop-discipline-design"></a>
<a id="approved-tg-m16-reduced-loop-discipline-trial-design"></a>

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

No reconciliation setup stage, Task seed, policy version, instruction-chain
inspection, target `AGENTS.md` mutation, persisted counter, alternate state
machine, or normal-loop call exists.

## Task Decomposition And Registration Design

<a id="task-decomposition-and-registration-design"></a>
<a id="current-m25-select-split-merge-register-design"></a>

The [Task decomposition and registration contract](task-operation-specification.md#task-decomposition-and-registration)
is implemented in Skill guidance and the task-workflow package reference. The
classifier is instruction-layer behavior; it adds no deterministic CLI,
repository interface, schema, Viewer, Runner, or command-inventory behavior.

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
- concrete coupling and the decision or use affected by separate completion,
  used by the one global Merge.

These values are not a database record, JSON contract, Task field, dependency
model, parser input, helper-owned worksheet, or prompt-log artifact. A fresh
agent boundary is satisfied when the final group's existing Task Contract,
routed authority, and declared predecessor outputs are sufficient; it has no
numeric token, file, line, or duration threshold.

The classifier runs this fixed sequence once:

1. `Select` the one explicit authority envelope.
2. `Split` once into a flat candidate set whose exact unions conserve the whole
   outcome and explicit permission envelope without omission or expansion, and
   whose sequence uses representable order. Choose boundaries for concrete
   separate-completion purposes, not just implementable or testable units:
   separate approval, early use or handoff, independent completion while other
   work is held, or a foundation that releases already-authorized downstream
   work. A foundation need not offer standalone user value. A provisional
   fragment may expose its missing state or gate independence for Merge.
3. `Merge` all concretely coupled fragments and same-outcome work whose separate
   completion changes no decision or use and mainly repeats gates. Form their
   transitive groups in one global pass, preserving explicit approval, order,
   and acceptance boundaries. Keep responsibilities separate when combining
   them obscures the mapping from acceptance to changes and checks. Multiple
   disjoint groups may merge at once; shared files or tests alone create no
   coupling. Ambiguous ownership invokes the fallback, not an arbitrary group.
4. Treat the resulting groups as final. There is no recursive classification,
   second Merge, re-Split, parent/child graph, or size/Task-count optimization.

Only final groups must each own one bounded responsibility and its authorized
inputs/outputs, leave a correct repository state after represented predecessors,
carry locally attributable verification and review, and be resumable from
Contract, routed authority, and declared predecessor outputs.
The combined scope retains all applicable gates. These criteria use the existing
session-local decision, not a scorecard, token estimate, split-reason output or
record, or extra confirmation step.

### Registration Adapter And Partial-Add Recovery

For explicit taskization, final groups use the existing `task add`, with its
structured stdin mode for a finalized multiple-Task set. The
group's non-zero Contract copies only explicit scope, acceptance, constraints,
and authority reference. Inputs and outputs remain prose in those existing
fields, while order remains existing lane/order; no dependency or worksheet
field is introduced.

For a new Task already authorized for immediate implementation, the instruction
layer carries its existing start decision into the existing initial `status`
input, using `in_progress` only when existing selection and predecessor order
permit that work. It does not infer permission from registration, a status value,
context selection/absence, or omitted display rows, and does not promote new
work around active or earlier ready work. Held work alone does not stop unrelated
ready work. The registration response's prepared context selects and supplies
complete Contract/gates; a ready preparation replaces a separate context call,
and an already active selection needs no redundant start edit.
Single/batch writers, initial Contract activation, sequential guards, and
read-only context composition remain unchanged. This is instruction guidance,
not a new eligibility helper, automatic start, or additional user decision mode.

`task_registration.py` owns the fixed version-one JSON decoder, strict member
types, explicit common-value expansion, and caller-value validation. It emits
only existing `add_task` keyword inputs; it neither assigns IDs nor reads/writes
state. `cli.py` rejects mixed individual options before state access, rejects
read-only before stdin consumption, reads the bounded binary input, then owns
the existing initialized connection and outer commit/rollback. It returns the
input-order index-to-Task mapping and one mutation outcome for maintenance.

After the writer commits and closes, `cli.py` prepares one context for the
whole successful registration through `connect_initialized_readonly`, retaining
ordinary global admission, then calls the existing `handle_task_context` with
fixed read-only arguments and that read connection. Its existing marker-2 show
path releases the read before physical selection and compares the Task on the
final bounded read. No resolver/storage admission rule or selection algorithm
is replaced. One closed `context_preparation` result separates successful
registration from a failed post-commit read; unexpected exceptions are sanitized.
It returns no partial context and triggers no retry or writer. Existing common
post-commit maintenance remains once, after command preparation; the embedded
context claims neither write/read atomicity nor a post-maintenance snapshot.

`tasks.py` factors single-add preparation from its existing row writer. Both
single and batch registration use that same row writer, Contract activation,
authority snapshot, events, stored validation, and sequential checks. Batch
prepares every normalized input, generated ID, initial Contract check, and
optional Effort Git observation before acquiring the writer; it does not skip
later Tasks' observations by preparing them inside a transaction. Under the
existing initialized writer, an outer batch savepoint encloses all row writes;
automatic lane orders see earlier batch additions, and locked Effort generation
and overlap checks remain unchanged. Failure rolls back the entire batch even
if a repository caller catches the error. The batch entry requires a connection
outside a transaction for preflight; the single-add calling contract is retained.
No schema, retry ledger, new state-admission rule, or Task lifecycle is added.

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
Skill applies the same completion-benefit and Merge criteria once to the addition
and its relationship to the current responsibility:

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

### Instruction-Layer Ownership And Synchronization

<a id="atomic-instruction-layer-synchronization-boundary"></a>

The concise trigger gate and disposition rules belong in
`task-governance-tool/SKILL.md`. The full one-pass sequence,
responsibility/fragment cases, Tier table, ordering examples, and recovery rules
belong in `task-governance-tool/references/task_workflow.md`. Package digests in
`task-governance-tool/release-manifest.json` track the shipped guidance under the
current version/release rules. Display metadata in
`task-governance-tool/agents/openai.yaml` describes the same registration and
scope-preservation triggers.

The product and implementation owners route the current instruction-layer
contract, while `plan.md` retains its separately owned decisions and static
contracts. Coupled checks belong in `tests/test_skill_self_containment.py`,
`tests/test_m14_integrated_acceptance.py`, and `tests/test_document_history.py`.
The classifier itself has no implementation in runtime scripts, migrations,
repositories, CLI parsing/output, or Viewer code/template, and introduces no
public command leaf. Each authorized change uses its applicable scope and
existing verification and review gates.

Consumer guidance uses the Skill as the concise invocation and conditional
entry point, the workflow reference as the single normal operation sequence,
and the CLI reference for exact input/output and failure contracts. Direct
section links select existing operations, states, or errors; they do not add
commands, user choices, confirmation, reading records, or limits. Examples use
the governed-project root unless an exception is stated locally and identify
the public response fields supplying Task IDs and target generations. Optional
checks follow, rather than precede, the ordinary operation.

Package-only users receive all actionable input, permission, gate, failure,
partial-success, retry, and audit guidance in those references. Internal schema
lineage, serialization, recovery stage mechanics, and browser state remain in
their existing formal owners, not duplicated as ordinary consumer reading or
replaced by a requirement to read development-repository documents. Related
checks validate package links, examples, and retained behavior without requiring
each rule or internal detail to be repeated in every consumer document.

`scripts/read_reference.py` is a standalone standard-library document reader,
not a taskgov command or runtime dependency. It resolves an existing
package-relative reference filename and heading/explicit-anchor fragment in
the three shipped references. It returns the selected heading subtree plus
ancestor introductions verbatim (line endings normalized to LF), retaining
links and their source-relative interpretation. Fenced examples are not
headings. Unknown or ambiguous fragments, invalid filenames, and unreadable
resources fail without returning unrelated text. It has no project discovery,
state connection, subprocess, network, write, or recursive dependency loading.
The Markdown remains the only instruction source; no duplicate prose registry
or natural-language operation classifier is introduced.

Host-access guidance is maintained once in the package CLI reference's
`Execution Access` section, with a short conditional entry in `SKILL.md`.
It describes effective host access and approval, not a taskgov permission
resolver, ACL editor, state relocation mechanism, preflight, or new error
mapping. Existing public inspection recovers uncertain write outcomes; existing
transaction and post-commit owners retain their behavior. Scenario review covers
read-only use, valid grants, required/unavailable/denied approval, unknown causes,
and uncertain outcomes without introducing prose-matching runtime checks.

### Neutral Forward-Test Boundary

Neutral forward tests for this instruction layer give fresh, minimal-context
agents the candidate Skill and neutral workloads without revealing the expected
branch or prior study result. A separate evaluator checks both the response and
resulting Task DB. The reusable coverage matrix includes:

- separately testable internal work on one outcome, multiple work phases, and
  small complex changes that do not need separate completion; distinct approval,
  independent outcomes, early handoff, and mixed acceptance responsibilities
  that do, including shared files/tests and an enabling slice for an already
  authorized consumer without standalone user value; concrete transitive Merge
  groups with no re-Split;
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
- unchanged current command leaves, schema, Runner, no additional normal-loop calls,
  target/evidence freshness, Handoff bounds, and no invocation from discovery,
  test failure, Effort, or cross-module failure alone. Each Task's review gate
  must pass independently of later integration review.

Positive, negative, and unknown cases use parallel wording and equal available
authority so the prompt does not reveal the expected result. A valid result
must match the specification branch and actual stored effects; self-reported
intent alone is insufficient. This matrix describes verification coverage;
whether forward testing is required for a particular change follows its current
Task Contract and the repository's existing validation rules.
