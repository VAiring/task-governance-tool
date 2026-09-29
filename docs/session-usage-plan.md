# Session Ownership And Usage Evidence Design

## Applicability And Authority

This is the conditional design and execution owner for the six already
registered Tasks in lane `TG-SESSION-USAGE-20260929`. It concretizes the user's
2026-09-29 session/usage agreement and subsequent authorization to consume those
Tasks and make local commits. It does not activate a runtime feature, migrate
the current database, install hooks, authorize Push/CI, or modify host settings.
Live progress and evidence belong only to the public Task CLI.

The current [authority index](authority.md) remains controlling. Each implementing
unit must update its durable behavior/structure owners in the same reviewed
change; until then those owners describe the existing product. In particular,
current schema 23 and the existing completion/review gates remain active.
The earlier discussion memo is input, not an additional current authority.
No token A/B run, pricing conversion, external service, daemon, automatic Task
completion, repository edit, or general session-management framework is added.

This plan remains active while any of its six Tasks needs its anchors. After
all are done or explicitly superseded, retirement requires the separately
authorized authority transition in [AGENTS.md](../AGENTS.md#documentation-maintenance),
with durable contracts retained and one immutable historical capture. Completion
does not itself authorize retirement.

## Delivery Order And Boundaries

| Order | Task | Input and coherent output | Changed responsibility and verification |
|---|---|---|---|
| 10 | `tg_task_8c2d0ad6222f7031` | Agreement and current owners → this conditional design | Documentation routing, state/permission/usage cases, document checker, exact diff, two independent Tier 2 reviews |
| 20 | `tg_task_e716b2e85e0c57b8` | Design → working ownership, migration, all command checks and read display | Task, CLI, database, completion and Runner write boundaries; independent-connection races, all state transitions and old-state preservation |
| 30 | `tg_task_1e4a617e9f425f49` | Design and session/execution identity → atomic numerical collection | Adapter, repository and migration; known numerical fixtures, duplicate/conflict/partial-tail/replay/concurrency/privacy tests |
| 40 | `tg_task_53aaed4361420d47` | Ownership and collected responses → reviewer participation and shared attribution | Existing review handoff/registration and usage relations; exact target, parent/independent reviewer, response boundaries, AB/BC and reopen cases |
| 50 | `tg_task_7ab5da8a1f7d1875` | Attribution → immutable usage evidence linked to exact completion cycles | Completion/Evidence/usage projection; collector failures cannot reject completion, late links, replacement and sealed-byte preservation |
| 60 | `tg_task_f54b556fa739ba95` | Preceding functions → tested lifecycle integration and introduction | Setup/state, packaging, conditional Skill instructions and host adapter; isolated hook/trust/flush/recovery and whole-flow functional tests |

The lane is sequential, with no optional internal stage. Each unit fulfills its
own declared tests and two fresh independent exact-target reviews; the last
unit does not substitute for earlier verification. A blocked lane does not
block unrelated ready Tasks. Do not describe intermediate collection without
attribution/hooks as a working automatic Task-cost feature.

The write scope is the responsibility in the table, its current owner, coupled
implementation/tests and package manifest. No new acceptance, execution units,
or permissions arise merely from a design suggestion. Unknown indispensable
host behavior must be resolved before the affected feature is activated; an
unknown is neither a PASS nor permission to invent a fallback.

## Current Owner Change Map

| Current owner | Implementation-time change |
|---|---|
| [Task operation](task-operation-specification.md#task-record-and-state-transitions), [structure](task-operation-design.md#task-state-and-selection) | Identity, ownership state, execution epochs, selection and all Task-scoped mutations |
| [Review/completion](review-completion-specification.md#structured-review-results), [native transaction](review-completion-design.md#native-done-and-reopen-transactions) | Locked completion authority; reviewer binding without fabricated provenance; execution-to-cycle linkage |
| [Evidence](evidence-specification.md#schema-v19-bundle-foundation-schema-v20v21v22-native-writer-and-evidence-json), [structure](evidence-design.md) | Separate append-only numerical usage evidence and exact-cycle links, never a gate basis or Bundle rewrite |
| [Database](database-specification.md#initialization-and-supported-schemas), [migration](database-design.md#migration-sequence) | Ordered versioned migrations, preservation/admission/backup/Viewer compatibility |
| [Setup/state](setup-state-specification.md#setup-contract), [resolver](setup-state-design.md#fixed-state-resolver) | Canonical usage projection location, explicit introduction/trust boundary and repair |
| [CLI output](specification.md#public-cli-and-output-contract), [privacy](specification.md#privacy-safety-and-stable-errors), [transactions](design.md#journal-and-connection-rules) | Small per-caller ownership/usage projection; closed metadata-only retention; no external work under writer |

Use the existing storage/repository layer. Keep identification, ownership
policy, log adapter, numerical collection, attribution, evidence projection,
and hook transport separate. Add modules by responsibility, not a generic
workflow engine. Existing global admission and selected-Task read semantics
are not relaxed to implement this feature.

## Session Identity And Unknown Identity

The command adapter obtains `CODEX_THREAD_ID` once per invocation, validates a
canonical UUID, and passes a typed caller identity to services. Do not use
`CODEX_SESSION_ID` as fallback: parent and child may share it. No user-supplied
CLI session selector or normal role/ID self-declaration is introduced.
Different `CODEX_SESSION_ID` is not itself a contradiction. A verified log or
hook identity that disagrees with the selected thread is a contradiction.

Missing/invalid caller identity does not prevent read-only inspection, help,
setup or the existing explicit administrative configuration operations. It
does prevent acquiring an execution, owner-only writes and completion with
`session_identity_required`; contradiction uses `session_identity_conflict`.
It is an ownership problem, not a measurement problem. Valid ownership still
allows completion if usage/log/hook identification fails. These IDs prevent
ordinary mistakes; they are not credentials or protection against malicious
environment spoofing, direct DB writes or direct project-file edits.

Ready/unstarted registration and organization keep their current user-authority
boundary. Initial `in_progress` registration and a batch containing it use the
same ownership guard as a normal start, atomically; two active Tasks for one
caller cannot be created through a batch. Initial `review_pending` is rejected
as specified below. No silent synthetic human identity is used.

## Execution Identity And State Matrix

Keep permanent Task ID, execution ID, owner generation, completion-cycle ID and
usage-response identity distinct. An execution begins with a successful start
of ready work; resume from pause/block continues that execution. Each owner
acquisition advances its generation, even for the previous caller. Reopening
a completed Task starts a new execution and retains the old cycle/execution.
Returning started work to ready or cancelling it ends its execution; a later
start is a new one. No time-based expiration or automatic takeover exists.

| State | Fixed owner / occupied slot | Completion authority | Ordinary mutation |
|---|---|---|---|
| ready, never started | none | none | Existing authorized organization; start acquires caller |
| in_progress | one caller; one such Task per caller/project | same caller and current generation | owner only |
| review_pending | no slot | immediately preceding owner and execution generation | completion owner; a write that returns to in_progress must reacquire a free slot |
| paused or blocked after start | none | none | Read or isolated resume; acquire caller and advance generation before further work |
| done | none | none | Existing exact reopen only; fresh execution plus existing fresh gates |
| cancelled | none | none | Existing permitted organization/restart rules, never reuse the ended execution |
| migrated active with unknown owner | unknown, not an invented owner | unknown, so none can complete | explicit recovery to paused, then ordinary resume |

New entry into `review_pending` requires an existing `in_progress` execution
owned by the caller. In one transaction it preserves that execution and owner
generation, moves the caller to completion authority, and releases its slot.
Initial `review_pending` registration, including any such batch item, is
rejected with `invalid_status_transition`; the entire batch rolls back.
Direct ready/blocked/cancelled-to-review-pending edits are likewise rejected,
as is the already-prohibited paused-to-review-pending transition. They create
no execution, generation, completion authority or temporary slot. Use the
existing start/resume to `in_progress` first; that operation alone acquires
an execution/owner and enforces the one-active-Task limit. Do not invent an
earlier start or silently select the caller as the previous owner.

This deliberately narrows the existing ownerless review-pending entry paths
when unit 20 activates ownership. It adds no step to the ordinary start/work/
review loop. Test both single/batch registration and every source state, with
and without another in-progress Task held by the caller: a free slot does not
make direct ownerless entry valid, and rejection never changes the other Task.
Repeating `review_pending` on an already known review-pending Task retains its
execution/generation and requires its completion owner; it does not acquire a
slot, even while that caller executes B. Migrated unknown review-pending work
uses explicit pause/resume recovery, not this no-op. Done retains its existing
write lock and exact reopen-only path.

An initial blocked Task has no execution until it actually starts. Migration
does not allocate existing active Tasks to the migrating caller, change their
statuses or delete duplicate active work. Mark active/review-pending ownership
as `unknown`; retain all old rows/cycles and allow explicit recovery one Task
at a time. Historical ownership and usage are unknown, not measured zero.

The cross-session recovery exception is exactly an existing edit to `paused`
with a nonempty reason, from in_progress or review_pending. Permit it with a
valid caller identity, including unknown legacy ownership. Do not combine it
with note, Contract, metadata, evidence, cancellation or completion changes.
Record the recovery actor/reason and invalidate the current owner generation.
The next ordinary resume acquires the new owner. Other owner-state transitions
remain subject to existing status/lane rules; recovery adds no hard deletion,
new reset operation, broad authorization or stale-age heuristic.

Review-pending X/A and in-progress X/B can coexist. X can complete A while
holding B without acquiring a second slot. Y cannot complete A: Y first uses
explicit pause recovery followed by the existing resume/acquisition transition,
subject to its slot and lane checks. An owner change does not erase still-valid
review evidence; stale commands are rejected using owner generation, while existing target/Contract
invalidation rules continue to determine quality-evidence freshness.

## Mutation And Concurrency Coverage

Capture the actor and observed owner generation with existing preflight basis;
revalidate ownership inside the same short `BEGIN IMMEDIATE` writer as the
business mutation. Never use a previous read/self flag as a capability.
Unique partial indexes enforce one in_progress owner per Task and one such
Task per session/project. Recheck after transitions and batch writes.

The owner check covers add-active/batch, edit including Contract-only edits,
notes, status/reopen, checkpoint, Effort writes, Task-associated handoff writes,
Runner Plan publication, target setting/restart, Verification Receipt insertion,
Finding insertion/resolution, thin completion and compatibility edit-to-done.
Review result/Receipt submission is the narrow participation exception below.
Until unit 40 activates that exception, unit 20 retains the current parent-
submitted review workflow but restricts registration to the execution's owner
or completion owner. It does not advertise direct third-party session submission
as implemented. Unit 40 adds the bound-reviewer path without opening unrelated
owner-only operations. This staged restriction must be explicit in the unit-20
owner documentation and tests, rather than silently bypassing the owner guard.
Project setup/maintenance and read-only diagnosis never impersonate a Task
owner. Pure reads, including completion check, perform no acquisition.

An automatic transition from review_pending to in_progress (for example a
Contract revision) must acquire the slot or roll back the entire change. It
cannot mutate the Contract first and later discover that X is executing B.
Completion preflight and locked native capture share the exact actor/generation;
takeover between them makes both completion routes fail with
`task_ownership_changed`. A different current owner uses `task_not_owned`;
a second start uses `session_task_in_progress`. Errors contain no other Task
prose or environment values.

The Runner retains its existing preflight/T1/execution/T2 transaction model.
Owner validation precedes T1. It cannot suppress mandatory cleanup after T1 or
discard already-created audit records if ownership changes during execution.
Cleanup/terminal recording is a narrow internal continuation of that admitted
attempt; it does not reacquire ownership, launch another attempt, or grant the
old caller a new Task mutation. New target/restart/completion calls use the new
owner generation. Test this boundary rather than placing a blanket check after
launch that leaves cleanup unfinished.

## Read Selection And Public Ownership

Every returned Task in list/current/next/show/context and write acknowledgements
gets one compact `ownership` object: `state` (`none|owned|completion_only|unknown`),
`owner_session_id`, `completion_session_id`, `execution_id`, `generation`,
`is_owner`, `is_completion_owner`. IDs are full or null, never abbreviated.
Self flags are calculated for that invocation; missing caller or unknown
ownership yields null, not false. Known none uses null IDs and false flags
when the caller is known. Never persist caller-specific flags in SQLite,
Evidence or a shared Viewer. IDs denote current authority, not process liveness.

Context selects only the caller's active/review-pending work, then ready work
using existing lane/priority ordering. Apply owner eligibility before the
bounded selection limit so 20 other owners cannot hide the caller's work.
Current/list/show can still explicitly display other owners and held work.
For missing identity, context reports no resumable owned selection and a fixed
identity diagnostic; ready inspection remains available but never authorizes
start. Preserve no-partial-result failures, row validation and compact budgets.

## Numerical Collection And Privacy

Collection reads only a registered project session's exact physical log segments.
SessionStart may register the invoking session for unassigned usage; ordinary
owner acquisition and reviewer read/save register their own identities. Do not
enumerate all unrelated log headers, recursively collect descendants, or infer
participation from parenthood. A supplied path is a discovery hint, not identity:
validate header thread/project and the allowed local source before reading it.
Relocation needs the existing binding confirmation, not path-based inference.

Use per-response `token_usage_record` with key `(provider, response_id)`.
Retain thread/turn IDs, source identity, model/effort, counters and collection
state only. Identical replay is a no-op. Conflicting counters, model or owner
for one key mark that response conflicting and exclude it from numeric totals;
do not choose last-writer-wins. Copied parent/fork history is not new usage.
Missing response identity or legacy cumulative-only data is an explicit gap,
not a delta inferred from a counter reset. No model/price conversion is made.

`input_tokens`, `output_tokens`, `total_tokens` are nonnegative integers with
total = input + output. Cached/cache-write input and reasoning output are
nullable subsets, not extra terms. Preserve missing breakdowns as null.
Store only bounded provider/model labels and validated UUID/response IDs.
Per-model totals cannot silently combine differently identified models.

Read complete bounded JSONL records outside the writer. Ignore message/tool
arguments, reasoning, full outputs and unknown fields; never retain raw lines,
hash private bodies, print rejected values or use an environment dump. Adapter
diagnostics are fixed codes. The experimental collector's whole-tree discovery,
root-only restriction and full-session accounting are not product behavior.

Commit response observations and the corresponding source cursor in one usage
transaction. Cursor identity includes source incarnation, verified prefix and
complete-record boundary, not a path/mtime alone. A partial trailing record is
not processed. Truncation/replacement starts a new incarnation and idempotent
replay; prefix disagreement is not accepted as append. Concurrent collectors
compare the previously read cursor under lock; a stale batch retries its read
on a later collection, never moves the cursor backwards or advances past an
uncommitted observation. Do not retry the user's Task mutation.

## Response Boundaries And Attribution

An explicit adapter must bind Task operation events to the generating model
response, then apply ordered successful ownership/reviewer events. Wall-clock
overlap, the latest filename, an adjacent token row alone, or an entire chat's
total is not such a binding. Existing machine Task-event identities returned
by CLI acknowledgements can serve as correlation keys only if the host's
structured tool-result envelope and response relationship are verified.
Do not parse natural-language output or shell source to guess Task IDs.

The source adapter returns either an exact `(thread, turn, response)` binding
or `boundary_unknown`. Unknown associations remain unresolved for later replay,
without preventing the Task operation. The implementation must prove its actual
host binding before claiming Task attribution; the local observations below
do not establish it. This is a required input to unit 40, not a new caller
command, mandatory prompt or permission to treat unknown as exact.

Responses strictly before first start remain unassigned. A response spanning
start/switch/completion is indivisible: associate its actual participating
executions, and retain a common/unassigned component if it also precedes start.
Do not assign an entire earlier discussion to the next Task. Shared totals
are whole-response totals, not exclusive per-Task costs; no arbitrary split
or duplicate sum is permitted. The common component is not a global graph
connector joining unrelated Tasks.

Pause/block ends ordinary owner participation at its bound boundary. Completion
keeps a closing participation through the bound final report in that turn,
including its delayed usage record, not every later turn in the same chat.
If work on B occurs in A's closing response, record AB sharing explicitly.
No final boundary or no confirmed response association means pending/unknown,
not time-based finalization. Reopen creates a distinct execution; it cannot
reconnect old and new work merely because the permanent Task ID matches.

Build components over execution participation and response/interval membership.
X={A1,B1}, Y={B1,C1} becomes {A1,B1,C1}, even with disjoint response sets.
{A1,B1} and {B2,C1} do not join through permanent Task B. Time overlap alone
never adds an edge. Recompute a component's union of response keys before
summing; never add previous aggregate totals together. Show the shared member
set and registered-participant coverage from every member Task. Unregistered
intervention and unrelated descendants are excluded and not claimed detectable.

## Reviewer Participation Without New Manual Steps

The existing helper's reviewer read/save captures the actual thread identity
and Packet Task/Contract/target binding; the reviewed Task's execution is read
from the same admitted project. Reviewer participation is separate from Task
ownership and occupies no owner slot. The reviewer cannot complete or edit the
Task, set its target or resolve another participant's Findings.

Bind automatic identity metadata in a separate machine transport record to
the unchanged original result digest. Parent submission forwards that record
with original bytes; it must not overwrite the reviewer with the parent's ID
or regenerate provenance. Direct single-Receipt/structured registration by the
reviewer captures that caller instead. A parent forwarding older results with
no machine binding cannot claim another session's cost; preserve the existing
quality result as declared and report reviewer usage as unbound. No new
LLM-entered session field, participation command or Skill read is introduced.

Reviewer identity used for permissions and independent-pass distinctness is
core review evidence, not numerical participation. Unit 40 owns main schema 25
and an immutable `review_receipt_sessions` relation keyed by Receipt ID, with
the actual session ID, execution ID, binding source (`direct` or `handoff`),
and the original-result digest for handoff (null for direct registration).
The referenced Receipt already owns project, Task, Contract and full target;
validate those relationships without duplicating its prose or provenance.
Store the binding in the same core transaction as Receipt, Findings and events.
Rollback leaves neither half; no cross-database transaction is needed.

Under that writer recheck Task, execution, Contract and full target
generation/value/base, reviewer binding and distinctness from the owner for an
independent review. For bound results, reject a different reviewer key using
the same session on the same Task/Contract/target. Serialize this check with
insertion, including separate concurrent submissions. Same-key submissions
retain existing Receipt/verdict rules and count as one reviewer; aliases cannot
create a second independent pass from a known identical session. Gate reads
use this core binding only, never the usage store or a cached usage projection.
Retain existing actual provenance/independence requirements; session identity
is not proof of fresh context, model execution or verdict. Child and independent-
root reviewers use the same mechanism. Save/submit failures preserve their
existing known/unknown commit outcomes.

Migration leaves old Receipts and their sealed evidence unchanged, with no
fabricated binding. Legacy-unbound results retain the existing trusted-caller
provenance and reviewer-key gate rules, not a claim of machine-proven session
distinctness; they grant no bound-reviewer exception to owner-only registration.
Do not assign the submitting parent's identity to them. New direct reviewer
registration captures its real caller, and bound handoff registration requires
the complete validated transport binding; a malformed/missing part of that
binding cannot silently downgrade to legacy-unbound. Usage-store failure never
converts a bound result into an unbound one. The supported legacy transport
without automatic identity remains explicitly unbound as described above.

Numerical reviewer participation is a replayable projection of committed core
bindings plus collected reviewer boundaries. It can be absent or delayed;
retry collection/projection, never Receipt registration. Core rollback produces
no accepted participation; a crash after core commit is repaired by replay of
the exact Receipt ID. Same-session participation is idempotent and stale
binding cannot be silently rebound. Test registration and completion with
absent/corrupt/busy usage state, concurrent aliases and both crash boundaries.

## Persistence And Completion Isolation

Reserve main schema 24 for ownership/execution state in unit 20. Add one
current ownership row per Task, append-only owner transitions and execution
records; store nullable completion-cycle association in a separate link table,
not by modifying old cycles. Constraints encode generation and status matrices.
Preserve existing Task/cycle/Bundle IDs, payloads and meaning. Migrate active
rows to unknown ownership, others to none; never synthesize past usage.
Update all consumers of the schema/source version and exact inventories in
that unit, including setup, recovery, global admission, Bundle source versions,
Viewer compatibility and realistic fixture tests. Existing old binaries reject
24 rather than bypassing ownership. Only explicit setup performs migration.

Usage failures must not become global Task admission failures. Use a separate
canonical generated usage SQLite file beneath the same validated current state
root, owned by the shared resolver, with independent versioned initialization
through explicit setup. This is a numerical evidence store, not a second Task
authority or alternate public `--db`. Main ownership/cycle records are the
authoritative input for attribution. Never attach the usage DB to the Task
completion transaction. Corrupt/unavailable usage state yields unknown usage
and does not relax or prevent valid core Task operations.

Main schema 25 in unit 40 adds only the core reviewer binding above and its
normal schema-consumer/migration updates. It does not move quality evidence
into the numerical store. Neither unit 20 nor unit 30 preallocates that later
feature as unused core scaffolding. Preserve old cycles and Bundle bytes under
the existing setup-only migration and admission rules.

Usage schema 1 (unit 30) owns registered source sessions, incarnations/cursors,
response records, conflicts and fixed collection diagnostics. Schema 2 (unit
40) adds operation/response bindings, numerical reviewer participation projected
from core review evidence, and execution membership. It owns no registration
permission or independent-pass gate. Schema 3 (unit 50) adds immutable aggregate snapshots, exact-cycle
links and explicit supersession edges. Unit 60 consumes these, not another
schema merely for installing hooks. Setup version-checks both stores without
making usage initialization failure a rollback of a successful core migration;
report the two outcomes separately. No ordinary read initializes either store.

The numerical store follows existing rollback-journal/short-writer rules.
Reads are query-only. All raw SQLite access remains in storage/repositories.
Backups/restore distinguish core and usage snapshots: usage capture can lag
but cannot claim consistency with a newer core execution/cycle. Replay verifies
project/binding and referenced core IDs; missing references remain unbound.
Core restore must not leave newer usage accepted against an older binding.
Restored numerical snapshots retain immutable observations, with current
association revalidated before display. No silent cross-project rebind exists.

## Immutable Usage Evidence And Display

Core completion records its execution/cycle relationship atomically with the
existing completion, even if numerical storage is absent. A usage worker later
reads committed core relationships and records an immutable usage snapshot
and links to those exact cycles. It never changes done state, owner, gates,
sealed Bundle bytes or cycle fields. A completion response reports usage as
pending/unknown where appropriate; post-commit usage failure is not core failure.

A snapshot contains format/algorithm version, project, component executions,
response-set digest and cardinality, model counters, registered coverage,
quality (`pending|complete|incomplete|conflicting`) and fixed gap codes. Full
response membership remains queryable in the numerical repository, not dumped
into every Task response. New observations or AB/BC mergers create a new
snapshot with explicit predecessor IDs. Old records remain; only the current
non-superseded component is counted. Linking the same snapshot/cycle again is
a no-op. A's earlier cycle may gain a link after B finishes, but A's later
reopened execution cannot receive that old link.

`complete` means all registered contributing execution/reviewer boundaries and
associated persisted response records are accounted for under a verified
adapter, with no outstanding gaps. It does not mean billing accuracy, hidden
provider operations or all unregistered intervention. Silence, a Stop alone,
a stable file size or elapsed time never establishes completeness. Unknown
tail/lost logs retain observed totals and incomplete/pending status.

The existing Task detail and audit surfaces expose usage summary/current
snapshot/cycle links, without adding a normal loop command. The shared
projection contains no per-viewer self flags. Use a separate canonical
`usage/index.json` and immutable snapshot files under the resolver-owned state
root, published snapshot-first/index-last; do not insert usage as a qualifying
Reference into an already sealed Bundle. Projection failure retains last-good
files and pending state. Usage-only update never invokes a Task state writer.

## Hook Integration And Unverified Host Boundaries

Candidate events are Stop/SubagentStop for incremental collection,
SessionStart for project-local startup/resume catch-up, and SessionEnd as a
best-effort supplement. No hook sends model context, requests continuation,
launches a model, or changes a Task. Return the event's neutral success shape;
never use blocking/continuation output for measurement. Parsing discards hook
message/input/output bodies and preserves only needed validated identities.
Parent `session_id` must not be mistaken for the subagent's `agent_id`.

Introduction requires the user to approve exact project-local hook files and
review/trust their actual definitions. No user-wide install, trust bypass,
overwrite of existing hooks or automatic installation during ordinary setup
is authorized here. Disabled/untrusted hooks are not functioning collection.
Package validation and mocked inputs cannot prove the running Desktop has
loaded/trusted or executed a hook. User scope, host version and supported OS
are recorded with the isolated integration result, not inferred from CLI help.

At a boundary, collect persisted complete records only. Later Stop or registered
project SessionStart retries unresolved tails and completed-Task work. An app
restart with no project session does not run catch-up. If no later event occurs,
late usage stays pending; explicit re-entry into that project's ordinary session
is the supported recovery trigger, not a daemon/timer or invented complete total.
An interrupted hook is replayable; source observations and cursor are atomic.
Concurrent/out-of-order hooks cannot roll progress backwards. One participant's
stop does not finalize a shared component whose other participants remain open.

### Evidence Available And Required Follow-Up

The official [hook reference](https://learn.chatgpt.com/docs/hooks) documents
trust review, lifecycle events and thread/turn fields. It also warns that the
transcript format is not stable. This supports a versioned adapter, not a claim
that a hook runs after all usage is flushed. The design makes no such assumption.

Read-only local inspection on 2026-09-29 observed CLI 0.157.1 and valid
CODEX_THREAD_ID; CODEX_TURN_ID/RESPONSE_ID/TOOL_CALL_ID/CALL_ID were absent.
Selected-session metadata contained response usage with response/thread/turn
IDs and separate tool records with call IDs, without a shared direct response
ID on the tool record. Only structural keys/counts were emitted; no transcript
content was retained. This does not yet prove the operation/response join.

Before unit 40 accepts automatic attribution, demonstrate the exact structured
join with sanitized host fixtures covering delayed records, multiple calls in
one response, start/switch/final report and compaction. If no unambiguous join
exists, stop that part for an explicit design decision; do not substitute time
or session totals. Before unit 60 claims automatic operation, independently
observe actual trusted hook delivery and last-usage flush/replay on an isolated
supported host. Windows Desktop, CLI and Linux/macOS are distinct observations;
currently no new hook execution/flush test or host-setting change is claimed.

## Verification Cases And Completion Gates

The first design Task verifies the complete agreement, the state/permission
matrix, existing consumer coverage and the following case outcomes, with the
document-contract checker, exact diff/links and two independent Tier 2 reviews.
Its completion certifies the bounded design and explicit unknowns, not runtime
availability. Later Tasks must demonstrate their implementation cases before
completion, as already required by their live Contracts.

| Case | Required result |
|---|---|
| X starts A then B without releasing A | B rejected, no partial registration/ownership/event |
| X pauses A; Y resumes; delayed X completion | A same execution/new generation; X rejected |
| X review-pending A while executing B | X may complete A; Y may not; Contract-to-active checks slot |
| Initial/batch or ready/blocked/cancelled/paused entry directly to review-pending | reject atomically, whether caller's slot is free or occupied; no fabricated prior owner; start/resume must occur first |
| Missing ID or several migrated active Tasks | no guessed owner; reads remain safe; explicit pause/resume recovery |
| Task completion with usage store absent/corrupt/busy | core gates govern completion; usage is pending/unknown |
| Core Task DB write fails | completion fails; no falsely reported core success |
| Child reviewer, independent root, duplicate alias, stale Packet | actual registered identity; one participant/pass; stale refused |
| Concurrent bound reviewer aliases or usage DB absent/corrupt/busy | core Receipt/session binding is atomic; alias rejected independently of numerical availability; valid registration/completion still works |
| Pre-start discussion, switch response and final report | prior responses unassigned; indivisible sharing explicit; tail remains pending until bound |
| AB and same B execution+C without duplicate response | one ABC component, union sum; old aggregate not double counted |
| Same Task reopened or unrelated time overlap | no automatic graph merge |
| A completes before B/shared finalization | later immutable snapshot links A's original cycle, not its new cycle |
| Crash before/after usage transaction; concurrent old cursor | atomic rollback/replay or deduplicated commit; cursor never regresses |
| Log truncation/replacement/partial line, lost response, disabled hook | preserve observations, no invented zero/completeness; scoped later catch-up |

No machine check parses natural-language agreement for semantic completeness.
Keep routing checks structural; independent review judges the design cases and
permission implications. Do not add regex canaries or a new mandatory full
suite for this documentation-only design Task.
