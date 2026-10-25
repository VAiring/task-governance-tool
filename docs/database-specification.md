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

## Conditional Review Continuation Persistence

Schema 29 introduces migration `review_continuations`; the current combined
implementation supports schema 33 with the collected-answer extension,
criterion-link repair, combined-slot and own-review lookups below.
Actual upgrade remains explicit Setup; managed host activation requires the
combined verification/review and host gates in the
[continuation plan](review-continuation-plan.md#sequential-phases-and-verification).
Ordinary commands never migrate; explicit Setup retains backup, exact predecessor
admission, transactional rollback, reentry and projection ordering.

`review_receipts.judgment_revision` is a positive SQLite integer, default one.
The unique key becomes `(task_id,target_generation,reviewer_key,judgment_revision)`.
A separate immutable judgment row exists for every Receipt. Existing Receipts
receive revision one, no predecessor and no continuation; no session binding,
verdict, Finding resolution, assertion, Reference, cycle or sealed Bundle is
invented or rewritten. Revisions above one require an admitted continuation and
the actual original reviewer session. Ordinary registration still admits only
revision one and rejects duplicates.

The concrete relation layout and guards are owned by
[continuation storage design](database-design.md#conditional-review-continuation-storage).
All native reads validate the complete relevant successor and resolution graph
before selecting effective results. A malformed, branching, cyclic, aliased,
cross-Contract, cross-condition or cross-target graph is
`evidence_ledger_inconsistent`, never a missing-table compatibility fallback.
Old unbound evidence retains its ordinary gate meaning but is not eligible for
same-session reassessment.

Continuation registration may pass one fully validated own-Task continuation
history and exact-basis judgment graph through the same writer's lexical,
write-free preparation interval. The borrow ends before any row write,
transaction exit, external effect or later gate; a different connection or Task
cannot consume it. Complete native graph checks and the post-registration gate
remain required.

Known settled cleanup can explicitly supersede an unjudged pending continuation
and admit its successor atomically. The old round, choices and any captured
unregistered originals remain history; superseded is not registered. Exact
captured v2 originals can join the successor's one complete atomic batch under
their old identity, with explicit capture/consumption relations. Missing worker
proof, unknown effects, lost saved results or an attempted reinterpretation of
an NG as unjudged prevent this transition. Migration creates no such history.

The migration rebuilds only the Receipt table, completion-cycle gate constraint
and Bundle format/source constraint, preserving each old column projection and
all old sealed bytes under the
[review-migration preservation rules](#review-migration-preservation).
It recreates the owned indexes and triggers exactly.
Unowned attachments to a rebuilt table fail admission before mutation; unrelated
standalone objects and all unaffected objects remain unchanged. Foreign keys are
restored in every exit path. Reentry validates both exact physical structure and
rows; a marker alone never admits a partial migration. Recovery and Viewer
admission use the same schema and graph validation.

New completion cycles requiring continuation evidence use gate basis version two
and a version-three Bundle. Other new cycles retain the version-one gate and
version-two Bundle. The independent
[Evidence format](evidence-specification.md#conditional-continuation-evidence)
never alters the meaning of a preserved cycle.

## Operation-Local Admission

Ordinary Task registration, start/state/metadata/checkpoint operations and
normal list/current/next/context/show/effort reads validate the current schema,
owned objects, migration history, project identity/binding and maintenance
metadata before their operation-specific reads. They do not audit every
Task's review, ownership-transition, retained-note or Runner history merely
to enter an operation. The same boundary applies to completion and repeats
under the existing writer lock. The public command surface is unchanged.

Within one current schema admission, the complete owned-definition validation
is shared by the Evidence ledger and Bundle schema checks. Their individual
column, foreign-key and relationship checks remain required. Standalone schema
helpers establish their own complete validation; no result survives the
admission call or crosses a connection, transaction or DDL boundary. Migration
history, additional owned attachments and migration temporary-object rejection
remain unchanged. Separate migration/reentry and publication stages retain
their independent validations.

Every consumed Task row and Contract pointer, current ownership/execution
relationship, note body, checkpoint and evidence relationship retains its
existing repository validation. Start and metadata writes need no review
history audit, including unrelated history on their own Task. Detail reads
validate their own complete review storage; completion retains the own-history
checks below. This is an operation's data boundary, not a certificate that
all historical data for that Task or the whole project is valid.

The ordinary reopen compatibility bridge follows this same boundary: saving a
partial legacy cycle validates its consumed projection, cycle matrix, ordinal,
selected evidence relationships and source-generation update in the writer,
without auditing unrelated Bundles. The existing reopen transaction still
validates the latest-cycle match, event links and new ownership/execution.
Partial history never qualifies as current gate evidence. Migration, recovery
and whole-project consumers retain their applicable global validation.

Required cross-Task predicates remain: same-lane predecessor status and
ordering, combined session execution/completion slots, and foreign-link
rejection for data actually consumed. Physical integrity/foreign-key checks
remain database-wide at resolver admission. Schema, journal, binding and
structural faults still fail closed. There is no caller-controlled scope,
persisted/cross-operation proof or automatic history repair.

Explicit Runner Plan actions retain global admission in preflight and under
their writer lock, including combined verification or Contract changes.
Review services use the same structural boundary and their original Task's
required review history, as defined by
[review admission](review-completion-specification.md#operation-scoped-review-admission).
This includes integrated Core reads and typed finalization completion at every
fresh snapshot and locked writer. Scope follows the admitted native Task and
prepared basis, never a public bypass or the Task command's display label.
Maintenance eligibility reads use the structural boundary and validated policy,
generation and managed-backup metadata. Disabled, current and not-due paths do
not audit Task histories. Such observations never certify an artifact: actual
whole-project capture/publication, backup and reconciliation retain global
validation at their existing locked effect boundaries.
Current Viewer capture composes that global admission with its complete
snapshot validation: global session bindings already validated by the full
Evidence Ledger are not scanned again, while Runner, collected-source,
judgment, ownership, retained-note and project metadata checks remain required.
This private same-read composition is not an operation-local source certificate;
older-schema reads and separate publication/outcome transactions keep their own
admission, as specified by the [Viewer owner](viewer-design.md#snapshot-and-publication).
Failed/deferred backup outcome-only writes also use structural admission and
the consumed maintenance metadata, repeated under the writer. They compare the
observed success generation, outcome basis and complete generation metadata
inventory before updating, as specified by
[same-process maintenance](setup-state-specification.md#same-process-maintenance).
Setup, doctor, migration/reentry, recovery and actual whole-project publication
retain complete applicable validation. The approved remaining work is owned
by [operation admission correction](operation-admission-plan.md).

Numerical lifecycle and Task usage projections apply the structural boundary
plus the ownership/cycle and actual-reviewer identities consumed by their shared
capture basis, without unrelated review-history audits. Their original-response
validation, transaction-local reuse and separate final publication guard follow
[immutable usage evidence](#immutable-usage-evidence); this does not narrow core
Evidence, Viewer, backup or recovery admission.

## Completion Review Admission

Ordinary completion (`task complete`, its read-only check, and compatibility
edit-to-done) validates review storage for the completing Task only. Its own
Receipt/session/provenance relations, continuation and judgment history, retained
originals, and applicable Findings remain subject to complete relevant-graph
validation. A reference to another Task's review result is inconsistent; it
never imports that Task's result or qualifies an independent PASS.

The scope follows relations from own Receipts as well as declared owners:
reverse provenance/source-arm consistency remains required for own version-zero
and version-one Receipts, including cross-arm collisions on their provenance
IDs. Dispatch inventories referenced by own Receipt sources
or entries are included even if their declared Task differs; that owner mismatch
is rejected without traversing the other Task's review history. Compatibility
edit-to-done selects this scope using the existing normalized status value.

The task-local queries start from own Receipt/task keys and retain all generations
and continuation states. They must not scan unrelated review history to select
that graph. Cross-arm provenance collisions are selected by either own Receipt ID
or own provenance ID, including a foreign v2 owner. Dispatch selection unions
declared ownership, own source references and own entry references and validates
each selected dispatch once. No project prefilter may hide an owner mismatch.
The three non-unique indexes in [schema 33](#schema-v33-own-review-lookup)
support these reads without changing the graph or duplicate-rejection rules.

Sequential eligibility still checks whether earlier Tasks in the lane are done
or cancelled. That status check does not authorize revalidation of their review
histories. Other Tasks' review rows and unrelated review orphans are outside
completion's review admission, even when those Tasks precede the completing Task.
The operation-local boundary above also excludes unrelated non-review history;
schema/object/migration history, project binding, selected ownership and
completion-basis checks remain.
Physical SQLite integrity and foreign-key checks remain database-wide; this
exception concerns review semantics, not admission of physically invalid storage.
The scoped review checks run again after acquiring the completion writer; no
cross-operation validation cache is introduced.
Within one selected review read or writer transaction for one Task, repeated
Receipt/Finding/Reference validation may borrow the same fully validated
immutable authority and manifest originals. Each mutable source and relation
remains freshly validated; no gate, ownership, resolution or Task projection is
reused. Scope exit, a transaction restart, a different connection/Task, or schema
change invalidates the borrow. This changes validation repetition, not the
selected graph, physical integrity checks or fresh completion writer admission.
After native cycle insertion, selected-cycle Evidence and Bundle validation
checks that cycle's persisted sources instead of replaying all Tasks' Bundles.

Existing whole-store consumers, including Setup, recovery and doctor, retain
global review admission and detection of unrelated history corruption/orphans.
Completion does not certify the whole database, repair history, or add a new
audit command, background auditor or public scope option.

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

Fresh setup creates schema v33. Structurally complete contiguous source schemas
v1-v32 are setup-only migration inputs; v33 is idempotent current state.
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
| v26 | combined executing/review-pending session slot and versioned interval semantics |
| v27 | forward repair of the legacy pending-to-active acquisition guard |
| v28 | validated Task note bodies retained separately from event summaries |
| v29 | explicit review continuations, immutable successor judgments and Finding-resolution links; frozen continuation Evidence |
| v30 | immutable collected-answer provenance/source and complete registration inventories; typed retained collected originals; Bundle 4/index 3 |
| v31 | forward repair admitting collected-answer producers on criterion links; source-31 Bundle 4/index 3 |
| v32 | non-unique partial expression index for the combined session slot; source-32 Bundle 4/index 3 |
| v33 | non-unique own-review history/dispatch reverse-lookup indexes and source-33 Bundle/cycle discriminator |

Each migration is transactional, idempotent, rollback-tested, validates
contiguous history and required objects/rows, preserves project/business IDs
and durable records, and passes `quick_check` and foreign keys. Acceptance
retains the realistic 12-Task/191-event fixture and historical completion/
review trace through every supported source version. No migration parses
private prose to invent structure.

History admission checks every stored marker, including earlier generations:
an integer version in the supported sequence, the exact migration name for
that version, and a text application time in the existing canonical UTC
second-resolution format. Extra zero/negative versions, wrong names or storage
types, invalid times and gaps are structural failures, not initialization or
repair requests. Future schemas retain their existing unsupported-version
rejection. Application times need not be increasing or ordered relative to
other records. Normal/global/local admission, Setup/Doctor, backup source/copy,
recovery candidates, migration/reentry and Viewer source checks retain this
same history boundary without replacing their structural or domain validation.

### Schema-v28 Task Note Bodies

Explicit Setup migrates schema 27 to 28 by adding nullable `task_events.note_body`
and three ownership/immutability guards. Schema 28 has 40 tables,
46 indexes and 76 triggers. Each accepted `task edit --add-note` body is retained
in full, up to 2,000 characters, on its corresponding event; the existing display
summary remains bounded to 1,000 characters. Note-bearing `note_added`,
`task_updated`, and `review_tier_changed` events save their body atomically with
Task edits, ownership changes and transition history. Body, event identity, Task,
project and event-type bindings cannot be rewritten, and retained bodies cannot
be deleted. Ingress and stored reads retain the common privacy validation.

Predecessor events receive null, explicitly meaning that a full body was not
retained, not that no note was supplied. No summary parsing or guessed recovery
is permitted. Migration preserves all predecessor column values, stable IDs,
unrelated objects and sealed Evidence payload bytes before and after its final
marker; failure rolls back the complete delta. Reentry validates without repair.
Hybrid predecessor schemas fail closed. Native source schema becomes 28 while
Bundle format remains 2; existing source versions and completion gates remain
unchanged. This adds no history expiry, reduced history window or required
caller operation. Old binaries reject schema 28.

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
v21 source invokes migration 22, followed by 23 through 33. Each later complete
predecessor invokes its remaining ordered migrations through 33; exact v33
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

<a id="current-schema-v25-persistence-contract"></a>

<a id="current-schema-v27-persistence-contract"></a>

<a id="current-schema-v28-persistence-contract"></a>

<a id="current-schema-v30-persistence-contract"></a>

<a id="current-schema-v31-persistence-contract"></a>

<a id="current-schema-v32-persistence-contract"></a>

## Current Schema-v33 Persistence Contract

Schema v33 is the public setup target. Its predecessor migration `24/task_session_ownership`
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
usage fields are added to sealed Evidence or Viewer v4. Viewer accepts v5-v33.
Reentry validates exact DDL, markers, retained graph and integrity without repair.
Setup retains its existing backup/rollback protocol; no normal command migrates.
Unsupported attached residue/hybrid state fails closed before writes. Global
consumers retain full admitted-row validation and recovery retains only its
existing verification-field-local exception.

<a id="review-migration-preservation"></a>

### Preservation Across Review Migrations

The supported Setup transitions 28 to 29, 29 to 30, 30 to 31, 31 to 32 and 32 to 33 preserve every
predecessor business and unrelated user table's column values and row identities,
including tables not rebuilt. Each identity remains associated with its original
values. Existing `sqlite_sequence` rows remain unchanged, preserving unrelated
tables' future AUTOINCREMENT identities. The inventory excludes only the literal
reserved `sqlite_` prefix, never a legal name such as `sqliteXkeep`.
`WITHOUT ROWID` tables retain their primary-key/value projection. A rowid table
whose identity cannot be addressed through an unshadowed SQLite rowid name or a
genuine integer-primary-key alias fails before the delta; an unprovable identity
is never silently excluded from preservation.

The old columns, identities and unaffected objects are fixed before the delta
and compared both after the delta and after final marker insertion. Intentional
new columns and relations are admitted by the destination's exact schema and
row validators; they do not replace the old projection. All pre-existing
migration-history rows remain identical. The sole permitted history addition is
the exact destination marker with its chosen timestamp. Marker-triggered changes
to predecessor data, old history or the new marker fail and roll back the whole
transition. Current-schema reentry validates without replaying earlier migrations
or retrospectively repairing their data.

### Collected Answer Persistence

Explicit Setup migration `30/collected_review_answers` requires admitted schema
29 and preserves all predecessor column values, row identities, unrelated
objects, and sealed Evidence bytes under the
[review-migration preservation rules](#review-migration-preservation).
It rebuilds only Review Receipts, Evidence
References and completion Bundles, then updates their coupled guards. An
unowned rebuilt-table attachment, conflicting name, incomplete marker history
or invalid predecessor fails before mutation; any failure rolls back the full
schema/row change and restores foreign keys. Reentry validates without repair.
Schema 30 has 50 tables, 47 explicit indexes and 101 triggers; schema 31 retains
that inventory. The exact schema-30 DDL and fingerprint remain its predecessor
admission contract, including its criterion-link producer constraint.

Receipt provenance is an exact union: version zero owns no provenance row;
version one owns only its existing normalized row/codes; version two owns one
immutable bounded canonical row in `review_receipt_provenance_v2`. Only a v2
independent Receipt may use `unable_to_review`. No migration synthesizes v2
answers or changes an old assertion, digest or model/Skill declaration.

Explicit legacy reviewer migration changes no native schema or historical row.
Its new operational continuation fence stores only the closed future-dedication
declaration defined by the [value owner](database-design.md#collected-answer-value-schemas).
Old round encodings, originals, sidecars, Receipts and sealed Evidence remain
unchanged; reading or retrying an old round cannot add a declaration.

`review_collection_sources` uniquely binds the exact preparation, source slot,
attempt, reviewer session, turn and message to one Receipt, basis/original/
payload digests and exact submission digest. A source item cannot register a
second Receipt. `review_collection_dispatches` and immutable per-slot
`review_collection_dispatch_entries` commit the complete 1..8-entry submitted
inventory, including retained legacy items, in the same public writer as all
Receipts, Findings, provenance, session bindings and References. The source's
deferred relation requires its dispatch inventory before commit. This inventory
is independent of recent-Receipt display limits; no response-loss recovery may
interpret absence as rollback or authorize replay.

`review_collected_captures` stores an immutable native metadata seal and the
deterministically derived path-free public projection for an explicitly admitted
consumer continuation/session. Its Receipt link can publish once. Source
preparation, complete execution basis, route, original slot/attempt/turn and
nullable initial-source context remain unchanged through later supersession.
A non-null source continuation must be a superseded ancestor, and its ID,
sequence and selection digest must match the source metadata during capture
admission and native reentry. Capture admission
requires separately settled source publication/effects and old wait cleanup;
core readers do not inspect operational files. Admission rejects selected-session
aliases, duplicate sources, dropped required captures and mismatched metadata.
Registered consumers require every capture consumed; superseded unused captures
retain null links as history. Native reentry compares the frozen projection
against its original metadata rather than treating an opaque seal as proof.

The narrow [rejected-answer replacement](review-completion-specification.md#rejected-unregistered-answer-replacement)
may retire an unregistered rejected capture instead of transferring it. Fresh
absence is repeated under the same native writer that admits the successor;
healthy captures transfer unchanged, and the old unused capture stays historical.
The operational fence retains the bounded rejection audit. No core schema,
Receipt, sealed Evidence or old original is rewritten by this recovery.

An explicitly typed late initial source in a version-two submission is not a
preparation capture. The registration writer requires its null source context,
unchanged basis, no prior judgment and never-selected reviewer, then commits
its initial Receipt/source and complete dispatch inventory together with the
current consumer's registration. Existing captures and their physical metadata,
projection, rank and one-time Receipt links remain unchanged. This transport
extension adds no physical schema migration or native capture-repair operation.

Source-30, source-31, source-32 and source-33 completions seal Bundle 4 and project index 3 under the
[versioned Evidence contract](evidence-specification.md#collected-answer-evidence).
They retain the mandatory exact execution/cycle link: global admission
and recovery reject a missing link without repairing it. This does not require
links for preserved completion history from before schema 24.
The existing gate basis remains version one without continuation or version two
with continuation. Backup, recovery and Viewer use the same exact schema and
row validation; ordinary commands never migrate or activate a host collector.

### Collected Answer Criterion-Link Repair

Explicit Setup migration `31/criterion_link_collector` requires exact admitted
schema 30. It adds `taskgov_answer_collector` to the existing criterion-link
producer constraint so a Contract's `review_assessment` link can retain the
producer of its collected v2 Review Receipt Reference. The link must still
match its Reference's assurance, producer and producer version exactly; the
repair changes no provenance, criterion meaning or completion gate.

The migration rebuilds only `criterion_evidence_links` and
`completion_evidence_bundles`, restores their owned attachments and updates the
coupled native-cycle guard. New native Bundles identify source 31, retaining
format four, index three, their digest domains and the existing ordinary or
continuation gate-basis matrix. It follows the
[review-migration preservation rules](#review-migration-preservation), retaining
every predecessor business row, unrelated object and sealed Bundle byte/digest.
It creates no links or evidence retrospectively and does not rewrite source-30
history or its schema definition.

The ordinary explicit-Setup backup and transaction boundary applies. Invalid
predecessors, conflicting temporary names or unowned rebuilt-table attachments
fail closed; any failure rolls back the full delta and restores connection
flags. Exact schema-31 reentry validates without repair. Current binaries
require migration for schema 30 rather than changing its DDL during an ordinary
command or treating same-version Setup as a schema repair.

<a id="conditional-schema-v25-reviewer-relation"></a>

### Schema-v25 Reviewer Relation

Schema 25 is the supported reviewer-binding predecessor. Its
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

### Schema-v26 Combined Session Slot

Explicit Setup applies `26/review_pending_session_slot` after schema 25. It retains
40 tables and 46 explicit indexes, adds three guards (73 triggers total), and adds
only `policy_version` to immutable owner transitions. Existing rows receive zero;
new transitions require one. Policy zero keeps the earlier in-progress-only usage
intervals; policy one uses the continuous in-progress/review-pending interval
defined below. No old closed waiting interval is retroactively filled.

New slot acquisitions check both owner states in the same session/project. Existing
legacy overlaps remain readable and can continue, release or complete under their
existing owner/generation; another acquisition is rejected until the conflict is
explicitly resolved. Migration chooses no winner and changes no owner, status,
execution, old transition value or sealed evidence. The Bundle discriminator and
native-cycle guard admit source 26/format 2; sources 19–25 remain unchanged.
Exact DDL/inventory, hybrid detection, preserved-row proof, marker-last rollback,
validation-only reentry and ordinary-command migration refusal remain required.

### Schema-v27 Legacy Reacquisition Guard

Explicit Setup applies `27/review_pending_reacquisition_guard` after exact schema
26. The already applied schema-26 definition and fingerprint remain immutable.
The forward migration replaces the combined update guard so a retained
`completion_only` to `owned` transition also requires all competing same-session,
same-project holdings to release first. Normal sole-owner transitions, release
and completion remain permitted. Preexisting holdings and all history are retained;
migration neither chooses an owner nor repairs business state. It adds no column,
usage policy version or gate (40 tables, 46 indexes, 73 triggers remain).

The Bundle discriminator and coupled native-cycle guard admit source 27/format 2,
preserving all source-19 through source-26 rows, bytes and digests. The index reports
the actual container. Exact source admission, marker-last row/object preservation,
copy/marker rollback, validation-only reentry, backup/recovery and ordinary-command
migration refusal apply unchanged. Old schema-26 writers reject schema 27.

### Schema-v32 Combined Session Slot Lookup

Explicit Setup applies `32/session_slot_lookup_index` after exact schema 31.
The non-unique partial index selects `state IN ('owned','completion_only')`
and keys project plus `coalesce(owner_session_id, completion_session_id)`.
It supports the existing service and INSERT/UPDATE acquisition predicates,
including pending-to-active reacquisition. No combined UNIQUE constraint or
owner repair is added; retained overlaps, same-Task exclusion, rollback,
release paths and interval-policy meanings remain unchanged.

The source-31 DDL and fingerprint remain immutable. The forward migration adds
one index (50 tables, 48 explicit indexes, 101 triggers), and advances only the
Bundle source discriminator and its coupled native-cycle guard to source 32,
retaining Bundle 4/index 3. Old business rows, identities, histories, guards and
sealed bytes remain exact. Marker-last preservation, failure rollback, exact
reentry, backup/recovery and ordinary-command migration refusal still apply.

### Schema-v33 Own Review Lookup

Explicit Setup applies `33/own_review_lookup_indexes` after exact schema 32.
Three non-unique indexes cover continuation `(task_id,preparation_id,sequence)`,
dispatch `(task_id,preparation_id,submission_digest)` and dispatch-entry
`(review_receipt_id,preparation_id,submission_digest)` searches. Continuation
indexing covers every state and generation; provenance ID lookups retain the
existing unique indexes. The inventory is 50 tables, 51 explicit indexes and
101 triggers.

Only the Bundle source discriminator and coupled native-cycle guard advance to
source 33; Bundle 4/index 3 and their digest domains remain unchanged. Schema-32
DDL, fingerprints, business rows/identities, migration history and sealed bytes
remain exact. The shared preservation proof runs before the delta and after the
final marker. DDL/copy/index/marker failure rolls back the entire transition;
destination conflicts fail before writes and current reentry validates without
repair. Backup, recovery, relocation, Evidence and Viewer admit actual source 33.
This creates no ordinary-command migration or persistent validation cache.

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
storage-helper reentry is validation-only; public setup continues through 23 to 28.
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

Numerical collection uses independent schema 9 in the resolver-owned
`current/taskgov-usage.sqlite`, not the main schema sequence. Explicit setup
initializes an absent store, migrates exact schema 1 through 8, and validates
current state. Normal reads and
collection never initialize, migrate or repair it. Incompatible structure,
WAL, corruption, contention or project/binding mismatch produces unavailable
usage, never a core admission failure or a weakened quality gate. Existing
core backup/restore does not copy this store or claim a paired numerical
snapshot. Attribution revalidates Task/execution references against restored
core state; immutable cycle-linked snapshots follow the contract below.

Schema 9 adds only an index on the existing attempt number, plus its migration
marker. Candidate ordering and partial-page coverage read only their supplied
source identities. The oldest-candidate/current allocation, failed-attempt
updates and monotonic watermark meaning remain unchanged. Maximum-attempt
lookup uses the index inside the same writer as its update or discovery CAS;
there is no separately cached counter. Migration preserves existing IDs,
observations, attempt numbers and schema-6-and-later continuations; schemas 1–5
retain their existing contextual replay rule. Old DDL and migration identities
remain frozen, and ordinary access never performs the upgrade.

The internal collector accepts one registered participant session and one
explicit physical segment under its allowed local source root. Header thread,
project location and provider must match; the project location also matches
the admitted store binding. There is no public registration command or model
call. The optional [lifecycle adapter](setup-state-specification.md#optional-lifecycle-usage-collection)
reads only registered participants; retaining an authorized pending-review
location below does not register that participant. Ordinary Task operations do
not collect logs.
Relocation cannot silently rebind numerical state: preserve it and report
unavailable against a different core binding.

Schema 8 separately retains source locations observed by the lifecycle adapter.
A locator contains the source/thread identity, allowed-root kind, the lexical
current-root fingerprint and a normalized relative path of at most 4096 UTF-8
bytes. These internal fields are not added to Task output or immutable usage
Evidence. Neither a saved location nor its root fingerprint proves the file's
physical identity or permission to read it. Each use rechecks the current root,
containment, physical path, header, project and thread through existing source
admission. A changed root never authorizes reuse of the old path. Candidate
capture may precede file/header creation and commits separately from numerical
collection, so an interrupted or failed read does not discard the location.

Locations for registered sessions have no fixed total-count eviction or time
expiry. A separate pending set may retain only an exactly matched native
collected-answer reviewer assignment, with its closed project/binding, Task,
Contract, execution/ownership, target, parent/reviewer, preparation/continuation,
slot and assignment/source-basis identities. It grants no log-body read,
numerical source registration, attribution or review evidence. A fresh exact
Receipt/source relation and existing participant registration are required before
promotion. Confirmed cancellation or incompatible native basis retires its pending
location unless its exact Receipt already exists. A changed operational slot on
the same native basis or an unavailable check preserves an unknown candidate;
a retained original can still receive its exact Receipt later.
Its lifetime follows the assignment, not an arbitrary elapsed-time cutoff that
would discard delayed Receipt registration. The pending set is capped at 1024
locations. Exhaustion preserves existing entries and reports the fixed numerical
unavailability reason `usage_locator_capacity`; it never silently evicts them.
Legacy review registration and unrelated descendants gain no new permission.

Selection uses finite indexed pages within the event's current allowed roots.
New candidates join the queue's end; an exact-revision attempt moves its entry
to the end before file I/O. Repeated equal hints do not reset its position.
This queue CAS is independent of the numerical cursor CAS: it records a fair
attempt, never evidence that bytes were read or a source recovered. Failed or
interrupted attempts remain retryable, and subsequent events can reach every
retained candidate for finite stable input when their work fits the available
budget. No later event, missed initial locations, arbitrary renames and unknown
historical segments retain the lifecycle adapter's existing coverage limits.
Directory inventory omission alone cannot mark a location under the current
roots missing; its direct attempt supplies that observation. Hash-only sources
and locators for an old root retain inventory-loss diagnosis. Neither a saved
locator nor an archive copy clears an existing source-loss gap.

Explicit migration adds empty locator storage without inventing paths from old
source hashes. Existing observations, conflicts, cursors, immutable snapshots,
membership and cycle links remain. Schema-6/7 collection continuations and an
existing schema-7 publication cursor are preserved; older schemas retain their
existing contextual replay rule. An ordinary event never performs this setup.

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
bounded labels/response IDs, counters, source identities, cursor boundaries,
the internal locators and pending-assignment identities above, and fixed
diagnostics; no conversation, reasoning, raw body or raw-line digest.

Complete JSONL records are parsed outside the writer. Normal collection resumes
from the committed complete-record offset with allowlisted model, session/turn
and legacy-coverage context. Physical identity and size detect replacement or
truncation; path, mtime, size or offset alone never prove prefix integrity.
An independent bounded, resumable audit rereads the sanitized prefix against a
fixed captured boundary/digest. This deliberately defers detection, including
same-length edits with restored mtime. Every source retains the explicit
`prefix_verification_deferred` diagnostic: finishing one audit is not proof that
the whole current source remains verified. There is no new completion gate.
Mismatch schedules a new incarnation and deduplicated replay; previous numerical
observations and conflicts remain. No subsequent project event means no progress
or detection guarantee. Continuous append cannot move an in-flight audit goal.

A partial tail remains unread; an oversized complete record creates a fixed gap.
Its drain position is separate from the complete cursor and retains no bytes.
When an oversized unfinished line has drained to EOF, an idle or zero-record
reentry retains `partial_tail`; only observing its newline permits complete-line
processing and the existing fixed gap. Neither reentry advances the complete
cursor or fabricates numerical observations.
Source changes during a slice commit no observations or continuation. Cursor,
parser context, coverage and dirty-response impact commit atomically after
checking both previous cursor and continuation revision. Stale concurrent
batches do not regress or advance unread positions; later collection retries
numerical work, never the user's Task mutation. Explicit migration preserves
old evidence and starts contextual replay in a new incarnation; old prefix
formats are not silently interpreted as new continuations.
Within one bounded ingest or audit scan, repeated saved-model lookups for the
same turn use the first result from that scan's admitted numerical read. A newly
observed turn context in the same scan takes precedence and alone enters the
changed context saved with the cursor. Missing models remain unknown; no lookup
result is carried into another scan, lane or incarnation.

After fresh core admission, changed interval geometry, turn ordering/conflicts,
boundaries, reviewer and actual-receipt bindings, source diagnostics, unresolved
operations and response/conflict observations select affected shared components.
Selection includes old/new overlap for merges and splits; completion periods stay
separate. Unchanged numerical aggregates are reused. Catch-up has the same
response-key unions, model totals, diagnostics and period association as full
projection for the same admitted input/basis. Global diagnostic changes may
require broad recomputation. Structural selection remains a full metadata pass;
no claim that every path is proportional only to appended bytes is made.

Current capture shares member/original lookups and overlap indexes within its
one admitted read state. This is transaction-local reuse, not a persisted
validation result; selected Task reads and the independent final-publication
guard retain the boundaries in [immutable usage evidence](#immutable-usage-evidence).
Within one response-attribution call, the geometry classification uses the same
response identity rows for boundary matching and typed response
identities. Full attribution still reads all response columns separately, and a
fresh publication guard obtains its own response observation.

Stored snapshot shape/digest, exact membership, original counters, supersession
and published immutable bytes are checked by the rotating publication audit
below. This explicitly defers historical integrity detection between events;
deferred log auditing is a separate mechanism. Budget or publication failure
keeps committed collection progress, completed audit steps and last-good output,
reports pending/unavailable, and resumes at later events without retrying
completion or review registration.

Repository summaries report observed per-model totals, registered source
coverage, conflicts and fixed gaps. Collection alone returns pending,
incomplete or conflicting, never final complete/billing accuracy. A failed
collection reports unknown while preserving any readable prior observations.
Unresolved source loss and replacement remain explicit diagnostics. Partial
tails and bounded-batch backlog are transient and disappear after catch-up.
Response-boundary attribution reads the same saved per-source pending state:
`partial_tail` remains a partial-tail gap, and unread or bounded-batch backlog
remains `collection_pending`. A source without pending work adds neither gap;
existing durable source diagnostics remain independent.
The lifecycle adapter consumes this same numerical foundation and attribution.

<a id="conditional-inclusive-turn-attribution"></a>

### Inclusive-Turn Attribution

This is the retained schema-2 through schema-5 policy and the meaning of
`inclusive-turn-components-v1` history. Current response-boundary collection
uses the following section; old snapshots and original counters are not
rewritten or presented as exclusive response partitions.

Schema-2 attribution follows the approved
[turn interval contract](session-usage-plan.md#turn-intervals-and-attribution).
Only explicit setup initializes or migrates the store. Explicit numerical migration
preserves schema-1 observations, conflicts and cursors; old readers reject the
new structure. A numerical migration failure cannot roll back core Task state.

Only a structured host turn identity plus a known successful public CLI Task
acknowledgement can propose a boundary.
Task registration's closed same-response references may supply their matching
Task/event bodies; malformed or identity-mismatched references remain unbound.
Revalidate the exact event, project, Task, owner generation, status and actor
against the committed core transition.
Missing/restored-away or contradictory references remain unbound. Never parse
event summary prose, command arguments or adjacent response timestamps to infer
which Task was active. The projection is not an authorization or quality gate.

Include every response in an explicitly identified covered turn. Both entry
and exit turns are whole, and subsequent observations from either turn remain
eligible. For schema-26-and-later transitions, `in_progress` and `review_pending` are one
continuous interval; their mutual transitions neither close nor restart it.
Pause/block/ready/cancel/done close it. Resumed intervals
accumulate until the next completion, including across a ready/cancelled restart
with a new execution ID. A later completion-only turn does not extend a closed
interval. Union turn/response keys instead of adding interval subtotals. A
completed Task's later reopened work remains a separate completion period.

The immutable transition's internal `policy_version` is zero for retained
pre-26 rows and one for newly recorded schema-26-and-later transitions. Version-zero
history retains its original in-progress-only meaning, including closed old
pending gaps; Setup never reinterprets that history. A new transition can
continue an already open interval into review_pending, but cannot reopen or
fill a previously closed gap. Pending responses are measured only from actual
observations; elapsed waiting alone creates no usage. Missing endpoints retain
the existing bounded unknown behavior.

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

### Collected-Review Usage Boundaries

For current response attribution, a registered collected-answer Receipt may
establish its reviewer's terminal turn from its validated native collection
source. The Receipt, reviewer session, execution, Contract, full review target,
original digest and source turn must agree with the stored provenance and
session binding. An operational capture without a committed Receipt supplies
no participation. The submitting parent's turn never substitutes for the
reviewer's answer turn, including retained and reassessed judgments.

This native endpoint replaces the absent legacy save event for that collected
Receipt; it does not fabricate a save acknowledgement. Legacy read/save and
direct-registration Receipts retain their own validated endpoint sources, but
current response attribution applies the same conservative ordering checks to
all three routes. Retained inclusive-turn readers keep their historical policy.
A matching observed review read starts the inclusive reviewer interval.
Missing read history retains only the
known endpoint turn with explicit partial coverage; missing or conflicting turn
order remains unknown. Both conflicting starts for one turn and equal starts
for different turns in the same session invalidate those turns' ordering.
Repeated identical observations and equal starts in different sessions do not.
Apply this ordering check before selecting either current review interval boundary.
The numerical store retains the first start and conflict identity for a turn,
not every contradictory start. A same-ID start disagreement therefore makes
review range ordering unknown throughout that reviewer session, even when its
retained value appears outside the range. It does not affect another session
or rewrite saved history. In that case, retain only an explicitly matching
read whose own identity is not conflicted. Exclude conflicted IDs before
checking the remaining read candidates for equal-start collisions, then count
the selected read by identity alone. A discarded peer timestamp cannot add
other turns or remove that definite read. Its observed response keys remain
partial assignments because the session's range ordering is unproven, even if
the retained first timestamps happen to look consistent. If no eligible read
remains, retain no inferred membership, including the native endpoint.
An otherwise unordered native endpoint must not discard an already observed
matching read: retain the definite read turn with partial coverage, without
inferring the intervening or later turns. Delayed numerical
records can fill the established range, but a stored endpoint alone supplies
neither token counts nor final coverage. Original response identities remain
deduplicated across all bindings.

For current legacy read/save and direct-registration attribution, an unordered
endpoint likewise retains only a safe matching read. A terminal boundary before
every safe matching read retains the earliest such read without inferring a
reversed range. A missing read with an otherwise safely ordered endpoint retains
only that endpoint; a missing endpoint retains only a safe read. These incomplete
legacy intervals count their definite keys as `partial`, never `exclusive`.
No eligible key means no inferred membership. Normal ordered intervals, identical
replays and independent sessions retain their existing meaning.

The validated endpoint metadata, native endpoint projection version and current
legacy interval policy version participate in the current capture basis.
Old captures require ordinary numerical refresh before
being presented as current. Refresh preserves prior immutable snapshots and
cycle links and publishes successors through the existing mechanism. There is
no core or numerical schema migration, extra reviewer action, host query, Task
replay or change to completion gates.

Within one numerical refresh and its admitted core read transaction, validated
review identities and endpoints may be shared by capture-basis calculation and
attribution. This removes duplicate retrieval and validation without changing
their identity checks or the captured input. The shared value contains only
typed metadata, not answer bodies. It is local to that update: a later update,
standalone read or publication guard obtains and validates fresh input. No
persistent verification cache or cross-transaction reuse is permitted.
Fresh input includes every consumed actual reviewer binding, including direct
registration and legacy handoff Receipts, not only collected-answer endpoints.
Their Receipt, Evidence Reference, execution and session relationships must be
validated before treating a capture basis as current or adopting an index.

### Response-Boundary Attribution

Schema 6 associates successful state operations with the numerical response
containing their original command. A later result or poll is a confirmation,
not a new start or a substitute origin. Exact event/project/Task/generation,
status and actor are still revalidated against committed core; a missing or
conflicting association stays unknown. Read-only selection acknowledgements
(`task.context`, `task.current`, `task.next`, `task.show`, `task.list`) are
separate observations and never synthesize a state transition.

Only admitted registered source records supply this association. Explicit
same-thread/turn response IDs may bind a call before its numerical values
arrive. The source-order bracket adapter is closed to the observed Codex
`0.162.0-alpha.17.2` and `0.163.0-alpha.10.2` formats: exactly one response in the call's turn between
its call and first matching result, without an invalid/foreign or conflicting
record. This is a supported observed format, not a guarantee for every host or
version. Other versions retain numerical values and may use explicit IDs;
unsupported or nonunique brackets remain unknown. No proximity timestamp,
ID prefix, command meaning or arbitrary transcript search supplies a link.
The first matching host output closes this bracket even when its body is
opaque, rejected, malformed or lacks a result for one batch position. Every
unresolved fresh slot in that outer call closes together. Later numerical
records cannot replace the retained candidate or fill a missing one; terminal
resolution and handle/poll continuation preserve that closed origin across
restart. Missing or invalid output-turn metadata also invalidates its proof.
Missing or malformed source-record/response-item discriminators and an opaque
response-item payload are invalid-record gaps, not ignorable unrelated records.
Lost call identity interrupts pending correspondence without discarding a
separately validated historical operation. Missing or invalid call-turn metadata
or tool-name structure has the same effect before an early return. A repeated
tracked call ID conflicts even if the later tool name identifies an unrelated
tool. This includes an origin retained only in live handles and already-issued
poll aliases after its original call/slot drains. Independent origins and
historical observations remain usable. Interrupted brackets stay closed; later numerical responses cannot repair
them. This affects pending proof only:
already confirmed observations and later healthy calls remain available.
Known unrelated record kinds and non-tool response items remain ignorable.

Call origin requires positive admission as a new execution or a poll of a
verified original execution. A direct `exec_command` argument object with a
literal command, or the sole `text(await tools.exec_command({...}));` wrapper
using the existing closed argument fields and JSON literals, admits a new
execution. The bounded exec pragma is optional and must fit one JavaScript
line comment ending in LF or CRLF. Its body cannot cross LF, CR, U+2028 or
U+2029; trailing executable code is never removed with it. The same boundary
applies to sole executions, sole polls and indexed batches. Unsupported input, including
empty input, extra statements, aliases and computed/alternate member spellings,
remains unknown; failure to recognize a poll never proves a new execution.
Even an explicit response ID only identifies the receiving wrapper's response,
not the origin of an unrecognized call. Unknown origin preserves independently
validated operation success and observed numbers; it does not mean failure,
unassigned work or measured zero.

Known direct JSON session/cell waits and a sole literal `tools.write_stdin`
exec wrapper can trace a verified handle lifetime to its originating call.
The observed indexed batch also admits exactly `const results = await
Promise.allSettled([...]); for(let i=0;i<results.length;i++){...}` with literal
`tools.exec_command` or `tools.write_stdin` argument objects and
`text({i,result:results[i]});` in that loop. The only optional loop statement is
`store("literal"+i,results[i]);` immediately before `text`. Whitespace and the
existing bounded exec pragma do not change correspondence. Static object keys
and JSON literal values are accepted; variables, spread, computed/duplicate
keys, other calls, altered result printing and additional JavaScript are not.
Batch rejection detects executable `Promise` references, including aliases
declared from that identifier, and conservatively rejects unknown identifier
escapes or template expressions outside the admitted grammar. It ignores
recognized literal, comment and regex text and preserves a validated
sole-literal poll. Line comments end at any JavaScript line terminator. A
slash is skipped as a regex only at a recognized expression-start position;
ambiguous slash contexts remain unbound, including postfix and property-name
contexts, rather than potentially hiding executable code. It does not evaluate
arbitrary aliases or computed global properties, or claim to recognize all
JavaScript compositions.
JavaScript poll literals must represent the same integer as their runtime Number;
rounded large integers are unbound in both batch and sole-literal exec wrappers.
The fixed input position, not arrival order, matches each whole indexed result.
Only outer script containers may expose those positions. Process-result
objects and process-exit headers keep their stdout opaque to slot discovery,
even when that stdout prints a valid indexed result. An enclosing process
cannot certify correspondence or termination of an inner batch slot.
An indexed result without an admitted matching batch input remains unbound.
Semantic decoding retains that unproven index even inside process stdout or
mixed acknowledgement containers. It may preserve an independently valid
operation, but cannot supply an origin or a new handle lifetime. An additional
indexed layer inside an already matched slot is likewise unproven.
Detected batch candidates outside the admitted grammar remain unbound even
when their output is flattened and has no index.
Poll slots retain their verified original response; fresh exec slots use the
outer call's response. Independent fulfilled slots remain usable when another
slot is missing, rejected or opaque; that slot remains unresolved. Duplicate,
Boolean, negative or out-of-range indices reject the correspondence before any
slot is confirmed. Duplicate JSON keys also reject the whole batch correspondence,
including keys decoded inside fulfilled values' admitted JSON strings and
output/content/text containers. Validate those values before registering any
handle or emitting any slot observation, including a repeated resolved slot.
The same validation follows a fresh batch slot's handle through a later sole
poll; rejection preserves its pending call and handle for a valid later result.
Rejected reasons and unknown containers remain opaque; no arbitrary body scan
is added to search for duplicate-looking text.
Distinct fresh slots are distinct handle lifetimes even when
they share an outer call ID; collisions invalidate their retained poll aliases.
A batch that yields an outer cell without indexed results remains unresolved;
this adapter does not infer or transfer its slots through that cell. Support is
limited to observed transport structure, not a host delivery guarantee.
Variables, unsupported compositions, reused/conflicting or absent handles
remain unbound, including retained origins and pending polls of a live
colliding handle. Opaque results and bounded continuation exhaustion retain
unknown coverage rather than establishing resolution or exclusive assignment.
Only supported terminal evidence resolves a tracked operation; lost-lifetime
coverage survives until safe source replay. Execution termination and Taskgov
success are separate judgments. A matching call, indexed fulfilled slot, or
known poll may resolve its lifetime from one complete supported process-result
envelope: an integer `exit_code` with optional string `output` and closed host
metadata, or the known `Process exited with code` header. Both zero and nonzero
codes terminate execution; neither establishes Taskgov success or an origin.
Ordinary, empty, JSON or incomplete-JSON stdout does not keep that positively
terminated process pending and never fabricates a successful operation.
An incomplete envelope, unknown container/header, conflicting live handle,
truncation marker, or multiple unindexed terminal results does not supply this
proof. An outer `Script completed` header alone does not prove an inner process
or batch slot ended; nested headers are validated at every layer. Repeated
metadata fields or invalid numeric header values are not unambiguous evidence.
Process stdout and Taskgov bodies cannot introduce running-handle lifetimes.
This also applies when an exit-code field is absent, null or invalid: any
process metadata identifies a process container before stdout traversal, and
only a fully validated running envelope supplies its top-level handle. Duplicate process JSON keys
cannot supply running handles, including for ordinary non-batch calls. A usable Taskgov
acknowledgement is not recovered from an object with duplicate keys in the
source JSONL either: the shared source decoder keeps that object opaque rather
than accepting its last value. Independently valid sibling objects and original
numerical records remain usable. The same object-level ambiguity rule applies
to decoded JSON strings and poll arguments; source object/string representation
does not grant different proof, including lifetime and handle decisions.
An opaque unused child of an otherwise validated acknowledgement is not a
damaged transport container. In ordinary output, a damaged independent block
does not invalidate a separately valid sibling's established origin; its
unresolved lifetime remains. Whole-correspondence duplicate rejection belongs
to an admitted batch or its retained slot, not ordinary index detection.
Malformed acknowledgement identities cannot
discard a separately complete valid sibling or certify body-only termination.
This includes a missing, malformed or inconsistent identity on either side of
the Task/event relation and absent project identity; a positively identified
foreign project is a different, irrelevant case. Registration entries within
one acknowledgement are independent validation units, for Task and review
registrations alike, including same-response
references. Keep each valid entry while retaining incomplete-evidence status
from invalid entries, regardless of their order. A valid sibling cannot erase
that status. Independent supported process termination remains separate proof.
The retained turn-only reader uses the same complete container admission,
including duplicate keys, incomplete trailing JSON and closed text-block shapes.
Recognizable review/read/save/wait leaves with missing required metadata remain
incomplete evidence too. Only their validated observation, not a status or
context-check label, can supply acknowledgement-based terminal proof. A
positively identified foreign project or sender remains irrelevant even if
other helper metadata is missing or invalid. Validate the excluding identity
itself before requiring those other fields; an absent or malformed identity
does not prove exclusion.
Text/input_text blocks must contain exactly
their type and string text fields; extra process metadata or unknown fields
cannot be bypassed by routing, semantic decoding, or handle extraction. A usable Taskgov
acknowledgement inside incomplete or conflicting process transport remains an
independent observation, never a substitute for the missing lifetime proof.
Only a body-only admitted transport may use the existing acknowledgement-based
terminal gate; unresolved transport siblings and output/content children of
mixed acknowledgement objects prevent that fallback.
An ambiguous live handle remains unresolved after a terminal poll; that result
cannot identify which colliding execution ended. Unresolved siblings and sparse batch positions retain
their existing capacity and unknown-coverage rules; bounds are not increased.
Headers, whole JSON bodies, text/input_text blocks, existing
exec output/content containers and the closed `{i,result:{status:"fulfilled",
value:...}}` result wrapper are recognized within eight levels and 2 MiB per
text. The depth limit spans outer containers, the indexed wrapper and its
fulfilled value together; crossing that wrapper never starts a fresh budget.
Separate complete blocks can remain usable when another block is opaque.
Partial JSON and unknown text appended to one body never yield early success.
Only the exact leading `Failed to find real location of <absolute Python
executable>` warning grammar may be stripped (at most four lines); its text
and path are never retained. Existing same-ack registration references remain
closed and identity-checked. No JavaScript or shell body is evaluated.

A successful start uses the first eligible selection response in its own
turn, regardless of the selected candidate's Task ID. A confirmation made
while another Task is active remains that Task's work. With no eligible
separate confirmation, start uses its own whole response. A start cannot
adopt another turn's preparation. Failed operations do not change the interval;
unconfirmed/missing state boundaries make their affected scope unknown rather
than proving failure or no assignment. Host interruption preserves observed
numbers and confirmed operations; it is not a pause or cancellation.

Manual completion retains the rest of its command turn until the next Task's
eligible origin. A structurally validated parent receipt supplies the actual
automatic-completion reporting turn under the same next-start cutoff. A next
start owns its entire origin response, including any previous completion report.
Multiple successful starts for distinct executions in that one response share
only that indivisible response. Earlier eligible preparation belongs to the
first successful start, not all starts in the shared response. Later
exclusive responses never join transitively through that sharing edge.
The eligible origin includes intermediate responses through the start, not
only the confirmation records themselves. Confirmations are restricted to
successful `task.context`, `task.current`, `task.next`, `task.show`, and
`task.list` acknowledgements with their public selection, Task or list shape.
Compact selection uses its existing `returned_count`, `total_matching`,
`limit` and `truncated` fields, rather than the noncompact list's `count`.
Context candidate references must use the closed same-response selected-Task
pointer and match that selected Task ID; no arbitrary pointer traversal occurs.

Response partitions use exact execution-owner sets. Reopened work has another
execution and completion period; pause/resume identity is not changed by the
collector. Successful pause/block responses remain in the stopped execution;
subsequent responses are unassigned until another valid origin, unless an
independent binding applies. Resume uses the same-turn eligible-selection
rule as start, retains the original execution and does not adopt a stopped gap
or earlier-turn preparation. A selection made while another execution is
active cannot become the new Task's origin. Stop and a single next start/resume
in one response give that whole response to the next execution; multiple
distinct successful starts/resumes share only that response. Within-response
state follows committed operation order, including stop/resume of the same
execution. Failed operations do not cut intervals; unconfirmed, missing or
ambiguous boundaries remain unknown rather than proving unassigned usage.
An execution-ending ready/cancelled transition with a null execution link is
matched to the immediately preceding same-Task core transition, never to an
unrelated current execution. An admitted operation/response association does
not prove placement in the response order: an unbound release or a stop in an
unordered turn fences the affected execution's possible continuation after
its last located state boundary until a located same-Task state boundary or
new local start/resume. No position inside the missing range is inferred.
Unrelated organization releases cannot end that fence or invalidate another
execution's independently allocated work, completion tail, preparation, or
confirmed unassigned gap. Turn-only fallback retains potential partial owners
but cannot override independently located response origins/exits or create
cross-Task membership on an already allocated ordered response.
Repeated starts/resumes of one execution in a turn retain each segment's own
located origin/exit. They do not authorize assigning unknown pre-start or
stopped-gap responses to that execution, or remove an applicable cutoff.
An unrelated blocked Task's ready/cancelled exit does not clear another
execution's completion tail or eligible preparation. Supported cross-session
pause recovery includes its qualified stop response in the original execution
(subject to next-origin priority). Core order proves release, but cannot supply
a clock-based cutoff in the former actor's response stream. After its last
independently bound active endpoint, possible continuation stays unknown until
its next qualified same-Task boundary or new local start/resume. Another
Task's organization update does not make that range exclusive. Independent
review/receipt participation remains retained; ambiguous overlapping ownership
is partial rather than a guessed exclusive or unassigned value.
An unconfirmed call fences its possible continuation from the admitted source
origin until a valid new local start/resume, or a located operation that
re-establishes every execution carried at that origin. A boundary is relevant
only when it enters or leaves covered work; an unrelated blocked/paused Task's
ready/cancelled organization cannot resolve the fence. With no carried execution,
an organization event cannot prove unassigned usage. A later validated terminal
result resolves its original call through the existing replay rules, rather
than treating missing results as failure. Independently confirmed preceding and
later execution intervals retain their original keys and numerical values.
Existing reviewer and wait-decision bindings
continue independently; overlapping original response keys are unioned once.
Unobserved numerical values are not zero; unknown assignment is distinct from
confirmed unassigned usage. Old unqualified history is not guessed into units.
Closed historical acknowledgements without call/response relations still
retain their verified turn interval. Observed values in that interval may
appear only as partial, potentially overlapping membership with unknown
assignment; they never establish exclusive response cost. Independently
verified reviewer and actual-receipt relations remain eligible.

### Review Wait Decision Attribution

The supervisor's first all-reviews-ended decision turn contributes its whole
observed usage to the structurally associated reviewed execution. This includes
OK, NG, mixed outcomes and missing originals, whether the timer is shortened or
kept because fewer than 90 seconds remain. Attribution does not depend on a
successful timer write. Unobserved parent receiving/resumption and user-facing completion
report turns, unrelated timer operations, and the supervisor's entire waiting
interval gain no participation from this rule. Existing owner/reviewer coverage
continues independently.

The conditional [wait helper](review-completion-specification.md#review-wait-usage-marker)
emits only the actual sender and validated project, Task, execution, Contract,
full review target and immutable artifact-manifest ID. The collector accepts it
only in that sender's structured host tool-result turn; a parent's forwarded
copy cannot substitute its turn. Message bodies, automation prompts, timestamps
alone and parent/child ancestry cannot establish this association. A marker is
a trusted caller's declaration of its decision, not proof of host completion,
review quality, ownership or permission.

Schema 4 stores these numerical observations separately from Task transitions
and reviewer Receipts. For one execution/manifest basis, repeated collection or
notification selects only the first explicitly ordered turn from the same
sender. Missing/conflicting turn order or different claimed senders remains
unknown rather than adding turns or choosing the latest sender. Union the
selected turn's original response keys with existing coverage; never add an
already counted owner/reviewer turn twice. Late observations of that selected
turn remain eligible through immutable successor snapshots.

Marker emission validates the Packet against one admitted current core read.
Replay revalidates the captured immutable manifest, its authority/criterion
Reference and original execution, including after completion or restore. It
never replaces missing identifiers with a new current target or reopened
execution. The original execution determines the completion period. Missing
anchors stay unbound; current snapshot reads also detect that changed core
basis. Numerical registration, collection or migration unavailability cannot
block timer handling, review registration or Task completion. Only explicit
setup migrates old numerical stores; there is no automatic historical marker
backfill or inference from old notification prose.

For the integrated finalization path, an exact public incoming-event receipt
may separately bind the actual resumed parent's turn to the retained original
execution/manifest, including when completion has already released ownership.
The wait worker supplies only the structurally observed turn and its immutable
prepared binding. Accepted sending, prose, an invented turn or the newest Task
owner is insufficient. Enabled optional collection uses the existing numerical
schema-4 single-turn relation and immutable-anchor validation; it performs no
setup/migration, core write, synthetic response or supervisor interval. Existing
union/deduplication and unknown/conflicting-source behavior remain controlling.
Late response collection links that real turn to the original completion period.
Receipt search may find that first receiving turn behind newer work for another
Task. It never substitutes the latest turn, execution or manifest. A turn shared
by several valid associations contributes its response keys once to the union;
late observations remain in each original execution's completion period.

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
The main schema is independently versioned (currently 33); no numerical table joins a core transaction or gate.
Optional lifecycle invocation follows the separate Setup/state contract and
cannot alter these quality or completion boundaries.

Current response partitions emit `taskgov-usage-snapshot-v2` with algorithm
`response-partitions-v6`. Retained `response-partitions-v1`,
`response-partitions-v2`, `response-partitions-v3`, `response-partitions-v4` and
`response-partitions-v5` snapshots retain their original policy meaning and
remain validated immutable history.
Changing the response algorithm invalidates
the capture basis and publishes successors without a schema migration or
reinterpreting old snapshot bytes. Versions 5 and later require positive origin admission;
legacy stored boundaries lack that proof and remain unbound until existing
bounded source audit/replay reevaluates them. Read-only access never performs
replay, and a permitted refresh cannot promote old boundary proof into the new
policy. Numerical observations, successful operation facts and immutable history
remain; correction requires no Task recompletion or review registration.
Source diagnostics follow the execution's admitted response membership and
validated additional reviewer/wait participation, including work that ends or
stops in the same response. Missing observations in a validated participation
interval retain its source diagnostics. Admitted ownership boundaries retain
source diagnostics even if the associated counters are missing or conflicting.
An unbound wait placeholder, including one with missing/restored-away anchors
or ambiguous senders, does not admit its claimed source merely by carrying
boundary diagnostics. Diagnostics cannot infer response
membership or draw from unrelated threads. Task periods, immutable snapshots
and published JSON carry the existing closed codes without changing numerical
values, original response keys or assignment semantics. Current source pending
state participates in that propagation by the same per-thread rule as durable
source diagnostics. Once collection clears a transient pending state, the next
capture follows the normal successor-snapshot rule; previous immutable
snapshots retain their recorded gaps.
All response algorithms keep the v1 fields and add `assignment`:
`exclusive` has exactly one execution, `shared` at least two, and `partial`
retains only known owners with explicit incomplete-boundary gaps. The canonical
digest includes this field and format. Old v1 bytes, membership and cycle links
remain immutable history. Supersession follows exact partition identity or
overlapping original keys, not transitive execution-graph merging.

The usage index is `taskgov-usage-index-v2`, retains existing index fields and
adds `attribution` and `unattributed` with separate `unknown`/`unassigned`
observed model totals. Existing Task usage detail retains its fields and adds
`attribution`, a `total` union over that Task's original response keys, and
per-period `work_units`. Each unit gives its `execution_id`, exclusive totals,
and shared/partial parts with snapshot IDs, execution sets, actual
`{execution_id,task_id}` members and observed model totals. These do not change
sealed completion Evidence or create a public usage command. No value proves
final coverage, billing, new ownership or permission.

Numerical schema 7 adds a restart-safe publication cursor. Each event captures
one admitted numerical/core basis, reuses its classification, and audits the
immutable history in stored order. A step validates the stored shape/digest,
exact members, original counters and predecessor edges, then compares or
durably publishes that snapshot's immutable bytes before saving progress.
The cursor is scoped to the exact prospective index (snapshot identities,
current identities, valid original-cycle links, attribution, core basis and
unattributed totals). A changed basis invalidates its reuse. Stale concurrent
advances fail closed; a failed file operation never advances that step.
After every item is visited, index encoding, observation of existing bytes and
durable temporary-file preparation run without core or numerical readers.
Fresh admitted reads then recheck the cursor, capture, source and structural
classification inputs and index membership, and remain open only through final
destination validation and atomic adoption. The prior physical destination
identity is revalidated; an absent destination must remain absent. Equal-byte
index replay also checks fresh database state and re-reads the same physical
file's bytes before adoption. A preparation or guard failure cleans up its
temporary file without advancing the cursor or replacing last-good output.
The existing cooperative deadline applies through preparation and the final
guard; temporary preparation does not reset or extend it.
Snapshot files always precede the index. If index publication fails, its
completed audit position can be retried on the unchanged basis.

A completed sweep is a record of past observations, not proof that all earlier
items remained unchanged. The index retains format `taskgov-usage-index-v2`
and adds `integrity={"policy":"rotating-audit-v1","status":"verification_deferred"}`.
Successful internal publication returns the fixed diagnostic
`evidence_verification_deferred`; `publication=current` describes index
publication, never integrity certification or complete coverage. The next
event after successful publication starts another sweep. Same-length edits
with restored mtime, changes to previously visited rows or files, and missing
earlier files may be detected only on that next sweep. No mtime/size check or
saved cursor is accepted as proof of unchanged bytes. Until a sweep finishes,
last-good output remains intact; without later events there is no progress or
detection guarantee. For finite stable input and enough budget to perform a
capture, one audit step and its checkpoint, repeated events reach publication
instead of restarting the same history prefix. Metadata classification and
current-component validation remain whole passes, and each snapshot/file is
still bounded to 16 MiB; this is not a universal latency guarantee.

Within one admitted numerical transaction, capture and display may share bulk
member/original reads and validated snapshot facts. Reuse never survives that
transaction, a new connection or event, and never replaces the fresh final
publication guard. Internal batch requests are bounded separately: combining
individually admissible snapshots must not apply one document's byte limit to
their original-key union. The existing per-snapshot/file limits remain unchanged.
Capture still validates all current components. Task detail
validates all current snapshot metadata before selecting by execution identity;
it then validates exact members, original counters and predecessor edges only
for components containing this Task's executions, including shared components
and current successors. Its total includes all its periods even when the normal
display shows only the latest period. Unrelated original counters remain subject
to the existing rotating audit. Common capture-basis ownership, cycle and actual
reviewer identities are still checked; this is not an audit of unrelated review
history. Missing or changed selected originals, tampered selection metadata and
stale restore/binding inputs remain unavailable, never verified by a cache.
Ordinary Task reads never advance this cursor, replay or publish files.
Explicit Setup alone adds the schema-7 publication cursor while migrating to
the current numerical schema, preserving old numerical observations, snapshot
bytes, membership, cycle links and schema-6 collection continuations. A newly
introduced publication cursor creates no trusted result for earlier history;
its first publication starts an audit from the beginning. The schema-8 locator
delta preserves an already existing publication cursor and its exact basis.
