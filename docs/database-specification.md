# Database Persistence And Migration Specification

This document owns database initialization, supported persistence, admission,
and migration behavior delegated by the
[product specification](specification.md#sqlite-migration-and-concurrency).
Supported predecessor schemas remain current compatibility contracts, not
historical authority.

The [implementation design](database-design.md) owns physical structures and
migration mechanics. The shared
[Runner protocol](specification.md#schema-v21-persistence-compatibility-and-shared-runner-protocol)
and [operational read/write boundary](specification.md#operational-readwrite-boundary)
remain in the root specification. Explicit setup and recovery operations
remain with the [Setup/state owner](setup-state-specification.md#setup-recovery-evidence-backup-and-viewer-maintenance).

<a id="migration-and-activation-boundary"></a>

## Migration And Activation Boundary

Schema v17 migration `verification_receipts` creates
one append-only Receipt table and adds the three internal completion-cycle
basis fields through the storage/repository layer. Receipt ownership, target,
uniqueness, link, and qualifying relationships are validated in SQLite and
again on read. Existing cycle rows receive only the version-0/null-digest/
null-link legacy discriminator; the migration synthesizes no Receipt from Task
verification prose, events, `verification_attestation`, completion cycles,
prior operational observations, command history, or review receipts. Existing
done Tasks and cycles therefore remain honest legacy attestation history. The
insert guard also preserves the pre-existing sole compatibility bridge's exact
`legacy_current_done` partial version-0/null/null shape while rejecting every
other new version-0 cycle.

Migration 18 `evidence_ledger_capture` adds immutable authority snapshots,
whole-field criteria and links, normalized Review provenance, artifact
manifests and entries, Evidence References, current snapshot pointers,
capture-version target bindings, and Verification subject columns. It creates
one exact current-basis legacy snapshot per Task but no historical target
binding, manifest, Reference, provenance row, subject, Receipt, Finding, or
cycle. Reentry validates exact ownership, digests, matrices, triggers, quick
check, and foreign keys without reconciliation or backfill.

<a id="initialization-and-supported-schemas"></a>

## Initialization And Supported Schemas

`setup` is the sole public initializer and migrator. No Task, handoff, review,
doctor, read, or write command creates/migrates a missing/old database.
Missing state is `db_not_initialized`; supported older state is
`migration_required`; a newer schema is `schema_too_new`. Old binaries reject
newer state and never downgrade/write it.

Fresh setup creates schema v25. Structurally complete contiguous source schemas
v1-v24 are setup-only migration inputs; v25 is idempotent current state.
Schema sequence is:

| Version | Durable addition |
|---:|---|
| v1-v4 | original Task/event/project and completion-evidence lineage |
| v5 | structured review target, receipt, and finding state |
| v6 | Git-snapshot base revision |
| v7 | local handoff outbox |
| v8 | immutable Task Contract revisions |
| v9 | optional Effort basis/activity metadata |
| v10 | maintenance opt-in, policy, latest backup/outcome/applied retention |
| v11 | managed backup generation ledger |
| v12 | append-only checkpoints |
| v13 | Viewer source/render generation and outcomes |
| v14 | stable identity, binding/history, and cleanup metadata |
| v15 | completion-cycle history |
| v16 | marker-only native capture activation |
| v17 | immutable Verification Receipts and completion-cycle verification basis |
| v18 | authority/criterion capture, Review provenance, target manifests, Evidence References, and Verification subjects |
| v19 | native completion Bundles, criterion links/Finding snapshots, and Evidence JSON projection state |
| v20 | verification Runner shadow storage and Bundle-v2 null-Runner tagged union |
| v21 | verification Runner gate-basis tags using the existing schema-v20 structures |
| v22 | retired Analyzer reservation cleanup in the existing Evidence/Bundle tables |
| v23 | explicit verification-not-required reasons on Tasks and immutable completion cycles |
| v24 | session ownership, immutable executions/transitions and execution-to-cycle links |
| v25 | immutable reviewer-session bindings in the core Receipt transaction |

Each migration is transactional, idempotent, rollback-tested, validates
contiguous history and required objects/rows, preserves project/business IDs
and durable records, and passes `quick_check` and foreign keys. Acceptance
retains the realistic 12-Task/191-event fixture and historical completion/
review trace through every supported source version. No migration parses
private prose to invent structure.

<a id="schema-v20-foundation-and-admission"></a>

## Schema-v20 Foundation And Admission

The migration implementation retains one non-public helper restricted to an
explicitly injected database path. It migrates one caller-owned disposable v19
database in
place, at the same path, inside one `BEGIN IMMEDIATE` transaction. It performs
no copy, backup, publication, managed recovery, or canonical-state operation.
Rollback restores the logical schema and data; SQLite file-byte identity is not
claimed.

Migration 20 is exactly `verification_runner_shadow`. It preserves every
existing v19 business row and stable ID, including the canonical payload bytes
and digest of each existing version-1 Bundle. Existing Task Runner markers are
zero and existing cycle/Bundle Runner basis fields are null; the four new
immutable Runner tables and new Runner Evidence rows start empty. Schema-v20
Bundle version 2 admits only `caller_attestation` or `not_required`, and its
Runner observation pointer is always null. Any Runner observation, Reference,
or criterion link remains standalone audit history and is never a completion
cycle or Bundle basis at schema v20. Every schema-v20 Runner record is
gate-ineligible version `0`. The migration owns only the complete physical DDL
and storage-parent integrity; plan, process, observation, cleanup, and service
admission are separate current Runner subsystem boundaries.
The Bundle table rebuild does not preserve or replay arbitrary caller DDL. A
persistent index or trigger whose name is not migration-owned but whose
`sqlite_master.tbl_name` is `completion_evidence_bundles` is unsupported
attached residue: successful migration removes it with the old v19 Bundle
table, while transaction rollback restores it. Unrelated standalone objects
not attached to the rebuilt table remain unchanged.
Marker-only, partial-owned-object, same-version owned-object drift, a known
later marker, busy/contention, integrity, or foreign-key failure is fail-closed
and leaves no partial migration.

The supported schema-v20 compatibility foundation retains the
Bundle-v2 null-Runner payload/serialization/digest writer, and schema-v20
compatibility for Evidence JSON, Viewer, and managed backup/recovery. The
schema activation itself creates no Runner resolution, attempt, sandbox event,
observation, Evidence Reference/link, Bundle member, or Runner projection.
Canonical database migration occurs only through explicit public setup.

Public admission distinguishes a complete v19 source from a hybrid before any
mutation. A database declared as v19 but containing any recognized v20-owned
table, explicit index, trigger, or column fails closed in the canonical
resolver, setup inspection/migration, Viewer, and managed backup/recovery paths.
Recognition follows SQLite's case-insensitive identifier equality: any catalog
object occupying a v20-owned table, explicit-index, or trigger name is a
collision, while a column marker is scoped to its designated parent table.
Generated columns are column markers under the same rule.
Complete-v20 migration admission accepts either empty Runner tables and
Reference/link sets or the exact [audit-only Runner graph](runner-execution-specification.md#parent-service-and-audit-graph).
It rejects a malformed, duplicate, foreign-owned, partially linked, or
gate-eligible Runner graph. Every Task Runner-basis marker remains zero and
every cycle/Bundle Runner-observation pointer remains null; native Bundle-v2
`caller_attestation` and `not_required` verification basis remains valid.
This check occurs before any database or sidecar write, migration backup,
recovery copy/publication, Viewer publication, or managed-backup write. A complete v19 source alone may invoke migration
20; a complete v20 source continues through migrations 21 and 22, and a complete
v21 source invokes migration 22, followed by 23, 24 and 25. Complete v22 invokes 23,
24 and 25; complete v23 invokes 24 and 25; complete v24 invokes 25, and exact v25
receives validation-only reentry. Unrelated extra
objects retain the existing policy, including deliberate removal of
unsupported unowned indexes/triggers attached to the rebuilt Bundle table and
preservation of unrelated standalone objects.

Schema v20 is an audit-only Runner foundation and never becomes a qualifying
Runner gate basis. The schema-v22 predecessor delta below retains the schema-v21
qualifying Runner protocol with explicit manual fallback.

<a id="current-schema-v21-persistence-contract"></a>
<a id="current-schema-v22-persistence-contract"></a>

<a id="current-schema-v24-persistence-contract"></a>

## Current Schema-v25 Persistence Contract

Schema v25 is the public setup target. Its predecessor migration `24/task_session_ownership`
adds `task_executions`, `task_ownership`, `task_owner_transitions` and
`task_execution_cycles` (39 tables, 45 explicit indexes, 67 triggers).
Execution/transition/cycle links are append-only. The one current ownership row
per Task has a partial unique session/project index for `state='owned'`.
States, full UUIDs, generations, same-Task execution relations and cycle relations
are validated on read under the existing global/selected boundary.

Migration preserves all existing business rows and sealed Bundle bytes. It creates
only ownership rows: active/review-pending are unknown, all others none; generation
is zero and IDs null. It invents no execution, owner, transition, usage or cycle link,
and does not change Task status or assign the migrating caller. Existing history
remains unknown, not zero usage. Explicit recovery is owned by the
[session state contract](task-operation-specification.md#session-ownership-and-recovery).

Only the Bundle table and its coupled native-cycle guard are rebuilt to admit
source 24/format 2. New native completion must link its execution to its exact cycle
within the same transaction. Older Bundles/cycles remain unlinked and unchanged;
format 2 is retained and the index reports the actual container. No per-caller display or
usage fields are added to sealed Evidence or Viewer v4. Viewer accepts v5-v25.
Reentry validates exact DDL, markers, retained graph and integrity without repair.
Setup retains its existing backup/rollback protocol; no normal command migrates.
Unsupported attached residue/hybrid state fails closed before writes. Global
consumers retain full admitted-row validation and recovery retains only its
existing verification-field-local exception.

<a id="conditional-schema-v25-reviewer-relation"></a>

### Schema-v25 Reviewer Relation

Schema 25 is the current public setup target. Its
`25/review_receipt_sessions` migration adds only the immutable relation defined
by the [reviewer binding contract](review-completion-specification.md#conditional-reviewer-session-binding).
It has 40 tables, 46 explicit indexes and 70 triggers. All predecessor business
rows, ownership, Receipt provenance, cycle IDs and sealed Bundle bytes remain
unchanged; old Receipts acquire no guessed reviewer session. The Bundle table
and native-cycle guard admit source 25/format 2 without adding Bundle members
or a numerical gate. Historical source-19 through source-24 evidence stays
readable. The format-2 index reports the actual container schema.

Only explicit setup can run the ordered migration. Global,
selected, Viewer, backup and recovery consumers preserve their existing scope
and fail-closed rules. Reentry is validation-only, with exact object inventory,
DDL, markers, relations and integrity checks. Partial-copy/marker failure rolls
back; no ordinary read creates the new relation. Numerical storage is neither
an admission prerequisite nor a participant in the core transaction.

### Supported Schema-v23 Declaration Delta

Migration `23/verification_declaration`
adds `tasks.verification_not_required_reason` (bounded text, default empty) and
the nullable same-named immutable completion-cycle column. Existing Tasks remain
empty and existing cycles remain NULL; migration invents no intent and changes
no old completion result. A new native cycle must copy its Task's reason: empty
for required verification, nonempty for an explicit waiver. Unspecified work
cannot newly complete. Only the existing partial legacy-current-done bridge
may insert NULL. The declaration rules belong to
[Task operation](task-operation-specification.md#verification-declaration).

Migration 23 retains complete v22 admission and its global Task/Runner/Bundle
validation, adds one cycle insert guard (35 tables, 42 indexes, 60 triggers),
and rebuilds only the Bundle table and coupled owned guards to admit source
23/format 2. Source 19/20/21/22 Bundle bytes and digests remain unchanged; native
completion at that source requires source 23. The format-2 index reports its container.
Reason text is retained in Task, Review Packet and completion-cycle storage,
not added to authority snapshots, Bundle payloads or Viewer v4. A standalone
Bundle therefore does not carry the waiver justification.

The transaction preserves original business columns, unrelated objects and
the new columns' empty/NULL defaults, including after the marker-last write.
Reentry is validation-only; partial schema, unexpected attachments or temporary
residue fail closed without repair. Setup keeps its existing pre-migration
backup and matched rollback; ordinary commands require migration and old code
rejects v23. All global consumers retain global admission, and recovery's
verification-only candidate-local exception does not include an invalid waiver
reason. Its supported Viewer projection adds no fields or calls.

### Supported Schema-v22 Delta

Schema v22 is a supported predecessor. Setup reaches it
through the existing ordered migrations from complete v1-v21 sources; v20
continues through 21 and then 22 rather than returning early. Exact-v22
storage-helper reentry is validation-only; public setup continues through 23, 24 and 25.
Ordinary commands never migrate or repair state.

Migration 22 is exactly `evidence_reservation_cleanup`. It rebuilds only
`evidence_references`, `criterion_evidence_links`, and
`completion_evidence_bundles`, restores their owned indexes/triggers and the
coupled Evidence cycle guard, and retains all business columns and the
35-table/42-index/59-trigger inventory. Current allow-lists remove
`derived_analysis` source/relation, `llm_derived` assurance, and `batch_analyzer`
producer; `deterministically_derived` and all other valid Evidence remain.
Complete source validation rejects unexpected retired-value rows and unowned
indexes/triggers attached to any of those three rebuilt tables before rebuild;
it never deletes, converts, or invents a replacement for such rows or objects.
Unrelated objects retain the established preservation policy.

Valid business rows, IDs, relations, provenance, completion history, and sealed
source-19/v1 and source-20/21/v2 Bundle payloads/source versions/bytes/digests
are preserved. The Bundle tagged union adds source 22/format 2 with the same
three basis arms as source 21 in the
[shared Runner protocol](specification.md#schema-v21-persistence-compatibility-and-shared-runner-protocol).
New native completion obtains source 22
from its locked database basis for both the stored Bundle and its payload;
the native cycle guard requires that source. No caller chooses it. The format-2
index reports the actual container schema 22 and may change bytes/digest while
referencing unchanged old Bundles. A source-22 Bundle is not admitted in a
physical schema-21 container. No format, digest domain, or evidence assurance
is upgraded by migration or projection.

Migration follows the existing foreign-key-off/legacy-alter-on transaction,
exact row/object preservation, integrity checks, marker-last, and connection-
setting restoration pattern. It rechecks preserved rows after the marker and
full v22 validation before commit, including effects of admitted unrelated
triggers. Failure restores logical schema/data, not byte-identical SQLite
files. Exact-v22 reentry rejects unexpected attachments and migration temporary
residue without repair. Setup retains its pre-migration managed backup and
matched package/database/artifact rollback boundary; older code rejects v22.

Current Task/Receipt/completion and Runner selection retain the schema-v21
[protocol](specification.md#schema-v21-persistence-compatibility-and-shared-runner-protocol),
including audit-only old graphs, no-relaunch recovery, live
implementation-drift invalidation, and self-contained done history. Setup,
doctor, backup/recovery, resolver/relocation, and Viewer admit v22 through their
existing boundaries. Recovery extends the same verification-only candidate-
local rejection rule through source 22; every other structural Task/Runner/
Bundle fault remains set-fatal. Viewer remains snapshot v4, accepts v5-v22,
validates and discards Evidence/Runner internals, and changes no public field
or UI. No CLI shape, Skill trigger/procedure/call, config, Runner runtime, gate,
or general migration framework is added.

## Numerical Usage Store

Numerical collection uses independent schema 3 in the resolver-owned
`current/taskgov-usage.sqlite`, not the main schema sequence. Explicit setup
initializes an absent store, migrates exact schema 1 or 2, and validates current state. Normal reads and
collection never initialize, migrate or repair it. Incompatible structure,
WAL, corruption, contention or project/binding mismatch produces unavailable
usage, never a core admission failure or a weakened quality gate. Existing
core backup/restore does not copy this store or claim a paired numerical
snapshot. Attribution revalidates Task/execution references against restored
core state; immutable cycle-linked snapshots follow the contract below.

The internal collector accepts one registered actual caller session and one
explicit physical segment under its allowed local source root. Header thread,
project location and provider must match; the project location also matches
the admitted store binding. There is no public registration command, transcript
discovery, model call, hook installation or ordinary-loop collection yet.
Relocation cannot silently rebind numerical state: preserve it and report
unavailable against a different core binding.

Per-response identity is `(provider, response_id)`, across segments and source
incarnations. Identical replay is a no-op. A disagreement in owner, turn, model,
effort or counters marks the key conflicting and excludes it from all totals;
never replace the first observation or sum both versions. Explicit parent/fork
history owned by a different thread is not child consumption. Cumulative-only
records in a turn without per-response observations (including mixed-format
segments) are gaps, not inferred deltas. Another turn's modern record cannot
hide that gap; inherited parent context is excluded. Legacy cumulative rows
in a turn with modern observations add no numerical value or completeness proof.
Active legacy context follows explicit session/turn context records, not the
turn of the most recently arriving response; delayed response provenance does
not move that context backwards. A missing legacy turn identity remains a gap.
Malformed recognized session/turn context records are fixed invalid-record
gaps, not silent permission to treat the preceding context as complete.
The legacy-only gap describes current unresolved turn coverage, not a permanent
historical fault. Recompute it through the committed complete-record boundary
on catch-up: delayed modern rows clear only resolved legacy gaps, not another
unobserved turn or an unrelated diagnostic. Bounded batches can remain pending
until that coverage is read. The reconciliation shares the observation/cursor
transaction and rolls back with it.

Input, output and total are nonnegative signed-SQLite-range integers, with
total = input + output. Cached/cache-write input and reasoning output are
nullable subsets, never additional terms. Missing breakdown remains null in
an aggregate containing it. Totals remain separated by provider/model; unknown
model is null, not inferred from another turn. An empty observation set has no
numeric model total, not measured zero. Metadata is limited to canonical UUIDs,
bounded labels/response IDs, counters, source identities, cursor boundaries
and fixed diagnostics; no conversation, reasoning, raw body or raw-line digest.

Complete JSONL records are parsed outside the writer. The source cursor uses
physical identity, incarnation, complete-record offset and a sanitized-prefix
digest. It never trusts mtime or path alone. Changed numerical prefix,
replacement or truncation creates a new incarnation and deduplicated replay.
A partial tail remains unread; an oversized complete record creates a fixed
gap. Source changes during reading commit no observations/cursor. The cursor
and observations commit together under a short writer after comparing the
previous cursor. Stale concurrent batches do not regress or advance it and
require a later fresh collection, never a retry of the user's Task mutation.

Repository summaries report observed per-model totals, registered source
coverage, conflicts and fixed gaps. Collection alone returns pending,
incomplete or conflicting, never final complete/billing accuracy. A failed
collection reports unknown while preserving any readable prior observations.
Unresolved source loss and replacement remain explicit diagnostics. Partial
tails and bounded-batch backlog are transient and disappear after catch-up.
This numerical foundation supports the attribution below, not automatic hooks.

<a id="conditional-inclusive-turn-attribution"></a>

### Inclusive-Turn Attribution

Schema-2 attribution follows the approved
[turn interval contract](session-usage-plan.md#turn-intervals-and-attribution).
Only explicit setup initializes or migrates the store. Explicit numerical migration
preserves schema-1 observations, conflicts and cursors; old readers reject the
new structure. A numerical migration failure cannot roll back core Task state.

Only a structured host turn identity plus a known successful public CLI Task
acknowledgement can propose a boundary. Revalidate the exact event, project,
Task, owner generation, status and actor against the committed core transition.
Missing/restored-away or contradictory references remain unbound. Never parse
event summary prose, command arguments or adjacent response timestamps to infer
which Task was active. The projection is not an authorization or quality gate.

Include every response in an explicitly identified covered turn. Both entry
and exit turns are whole, and subsequent observations from either turn remain
eligible. Any exit from `in_progress` closes the interval. Resumed intervals
accumulate until the next completion, including across a ready/cancelled restart
with a new execution ID. A later completion-only turn does not extend a closed
interval. Union turn/response keys instead of adding interval subtotals. A
completed Task's later reopened work remains a separate completion period.

Execution IDs remain the sharing graph nodes: AB and the same B execution/C
merge even when their response sets do not intersect. A later B execution does
not connect those groups by permanent Task ID. Different threads' overlapping
clock times create no edge. Task cumulative views can include several separate
components without merging those components. Their totals describe registered
shared work, not exclusive per-Task cost or all unregistered intervention.

The adapter retains only validated turn starts, successful operation identities
and numerical metadata. It discards all remaining tool-result text before
hashing or storage. Turn order comes from explicit host `task_started` records
for known turn IDs, not an estimated Task-time interval. Conflicting identities
are not last-writer-wins. A lost/cross-thread endpoint retains only a definite
observed endpoint with an unknown boundary, never unbounded future-session usage.

### Immutable Usage Evidence

Numerical schema 3 adds immutable aggregate snapshots, original response-key
membership, exact completion-cycle links and predecessor/successor edges.
Capture unions original keys, never sums overlapping aggregate totals. Each
Task period includes all its executions since its preceding completion,
including ready/cancelled restarts; a reopened period is separate. A snapshot
can link to every completed period containing a member execution, including
earlier completed Tasks whose shared component grows later. Repeated capture
and links are idempotent. Changed components supersede overlapping current
components; original snapshots and links remain immutable. Only non-superseded
components contribute to a current total, including after a split or merge.

Snapshot format `taskgov-usage-snapshot-v1` and algorithm
`inclusive-turn-components-v1` contain only project ID, execution IDs,
response-set digest/cardinality, per-provider/model counters, registered-only
coverage, quality, fixed gaps and predecessor IDs. The snapshot ID is the
SHA-256 of canonical sorted-key compact UTF-8 JSON without that ID. The response
set digest hashes sorted `(provider,response_id)` pairs using the same encoding;
full membership stays queryable in the numerical repository. An empty model
list means no measured total, not measured zero. Nullable breakdowns stay null.
Current adapters emit `pending`, `incomplete`, or `conflicting`, never `complete`:
no verified end-of-coverage proof exists yet. Silence, Stop, elapsed time and
stable file size cannot supply it. Totals describe registered shared work,
not exclusive Task cost, all intervention or billing accuracy.

Capture reads an admitted core snapshot after acquiring the numerical writer;
it performs no core write, log I/O or file publication under that writer.
Capture and display revalidate immutable execution/cycle/reviewer identities.
A core-basis mismatch after restore or later transitions returns unavailable
until replay; missing references are never rebound to the newest Task cycle.
Restored numerical state with a different project/path binding stays unavailable.
The main schema remains 25; no numerical table joins a core transaction or gate.
Automatic lifecycle invocation remains the separate integration unit.
