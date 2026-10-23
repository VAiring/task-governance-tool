# Database Persistence And Migration Implementation Design

This document owns database physical structures, migration, reentry, and row
preservation delegated by the
[implementation design](design.md#canonical-state-and-sqlite-boundary).
The [database specification](database-specification.md) owns supported behavior.

`storage.py` composes migrations, recognizes schemas, and owns initialized
admission. Its existing `schema_*` modules supply ordered SQL definitions;
the [runtime module boundaries](design.md#runtime-module-boundaries) identify
those modules and the concrete repositories that consume admitted connections.
[Journal and connection rules](design.md#journal-and-connection-rules) remain
shared, as does the [Runner protocol](specification.md#schema-v21-persistence-compatibility-and-shared-runner-protocol).
Setup orchestration remains with the
[Setup/state design](setup-state-design.md#setup-doctor-backup-and-maintenance).

<a id="migration-sequence"></a>

## Conditional Review Continuation Storage

This is the schema-29 continuation foundation, preserved and extended by the
schema-30 collected-answer storage and schema-31 criterion-link repair below, specified by the
[database owner](database-specification.md#conditional-review-continuation-persistence).
`schema_review_continuation.py` owns exact DDL, predecessor migration and physical
admission; `review_continuation_repository.py` owns transaction-bound relation
reads/writes; `review_continuation.py` owns pure values, closed decoding and graph
selection. Neither feature module opens SQLite or commits a caller transaction.

Physical admission constructs the expected owned-object name inventory once per
validation and shares it between fingerprint and attachment checks. Migration
similarly shares one inventory within its attachment scan. This is call-local
reuse of code-defined names, not cached database validation: each invocation
still reads and validates the current schema and every relevant attachment.

The closed core relations are:

| Table | Columns and constraints |
|---|---|
| `review_continuations` | `continuation_id` primary key; `project_id`, `task_id`, `target_generation`; `preparation_id`; `sequence` integer 1..64; nullable unique `predecessor_id` self-FK; `basis_json`, `basis_digest`, `selection_digest`; `route` manual/integrated; `state` preparing/prepared/registered/superseded; `created_at`, nullable `registered_at`, `retired_at`, `retirement_digest`; unique preparation/sequence. Superseded requires retirement time/digest and one explicit successor; other states have null retirement fields. |
| `review_continuation_reviewers` | Primary key continuation/session; `session_id`, `action` supplement/reassess, nullable `prior_receipt_id` FK, privacy-validated `reason` at most 1,000 characters; nullable `result_receipt_id` unique FK and `original_result_digest`. Supplement has no prior; reassess requires one. Result and digest become non-null together, only once. |
| `review_receipt_judgments` | `review_receipt_id` primary/FK; positive `judgment_revision`; nullable unique `prior_receipt_id` self-chain Receipt FK; nullable `continuation_id` FK. Revision one has no prior; later revisions have both prior and continuation. Receipt/revision is unique and the Receipt has a deferred composite FK to this relation. |
| `review_reassessment_resolutions` | Primary key finding/successor; `review_finding_id` FK, `successor_receipt_id` FK, `continuation_id` FK; explicit `resolution_summary` at most 1,000 characters, `resolution_digest`, `created_at`. Rows are immutable; a retained resolved Finding is not updated a second time. |
| `review_continuation_retained_originals` | Primary key continuation/session; `continuation_id` FK, `prior_continuation_id` FK, `session_id`, `original_result_digest`, `metadata_digest`, nullable `result_receipt_id` FK. The prior is a superseded ancestor with the same complete basis and selected actual session; it is not selected again in the consumer. Hashes and identities are immutable. Only its exact atomically registered original publishes the receipt link once. Superseded consumers may retain unconsumed captures as history. |

Identifiers follow existing tool-generated prefix plus sixteen lowercase hex
digits; preparation IDs use `tg_review_preparation_`, continuations use
`tg_review_continuation_`. UUID session IDs use the existing canonical validator.
Digests are sixty-four lowercase hex digits. Canonical UTC strings retain the
existing time contract. The basis JSON has a 16,384 UTF-8 byte bound and exactly
`version=1`, `project_id`, `task_id`, `contract_revision`,
`authority_snapshot_id`, `acceptance_criterion_id`, `verification_criterion_id`,
`artifact_manifest_id`, `review_tier`, `verification_requirement`,
`verification_not_required_reason`, `completion_commit_required`,
`target_kind`, `target_value`, `target_base_revision`, `target_generation`,
`project_path_hash`, `project_binding_generation`, `parent_session_id`,
`execution_id`, `ownership_generation`. Nullable criteria retain actual absence.
The SHA-256 domain is `taskgov-review-continuation-basis-v1\0`; selection digest
uses `taskgov-review-continuation-selection-v1\0` and the sorted closed selected
reviewer rows without result fields, tool IDs or timestamps.

Quality selection uses project/Task, Contract, authority/criteria, manifest,
review tier, verification requirement/reason and the four-part target. Parent,
execution, ownership, filesystem binding and commit-required policy constrain
continuation/effects separately; changing them cannot manufacture Git authority
or transfer an intent, but does not turn otherwise valid quality judgments into
a need for redundant reviews. The writer checks both the quality and applicable
effect binding before the corresponding operation.

SQL guards make identity, basis, predecessor and selections immutable, disallow
deletion, and allow preparing to prepared to registered, or explicit pending
supersession with retirement time/digest and one successor admitted atomically.
The retirement digest uses `taskgov-review-continuation-retirement-v1\0` over
the old continuation ID, complete basis digest, nullable immutable request digest
and nullable bound worker-join checksum. Only operationally settled proof reaches
this repository; the checksum is structural evidence, not authentication.
Saved slots not selected anew are captured by exact original-body digest and
`taskgov-review-continuation-retained-metadata-v1\0` over the complete canonical
v2 sidecar. Already captured originals must survive further pending supersession;
required slot count and actual same-session reassessments cannot be lost.
A superseded row remains distinct from registered and invents no missing result.
Only its captured old original may publish once through its prepared descendant's
complete atomic batch. A registered consumer links every capture to the actual
old-context Receipt; superseded consumers leave unused captures null as history.
A prepared registration inserts the Receipt, provenance,
References, actual session relation, Findings and judgment metadata in the same
writer. The repository compares the expected effective head, complete captured
basis, actual session and reviewer key before publication. Supplement cannot
hide an already registered result. A successor increments exactly one and has
the same actual session, key, Contract, authority/criteria and four-part target;
one predecessor has at most one successor. One actual reviewer counts once.

Global admission validates the full graph, even when display uses a bounded
historical page, within the operation's admitted scope. Completion supplies its
Task ID through the internal `review_task_id` storage argument on each initialized
read/open and locked writer revalidation. The storage layer keeps ordinary
structural admission and scopes session binding, collected-answer and judgment
validation to that Task. Repository `review_task` is the resolver-derived
project/Task pair, never a public caller-controlled bypass. Own judgment rows
retain all generations and are acquired in existing validation chunks; linked
predecessors and results must belong to the same Task/basis. Global callers omit
the argument and retain whole-store review/orphan validation.

Ordinary Task and review-service adapters mechanically select internal `operation_local=True`
under [operation-local admission](database-specification.md#operation-local-admission).
The resolver and each initialized connection/writer still validate the exact
schema and project binding, but do not invoke whole-project Runner, ownership
or note validators. Existing selected repositories validate their consumed rows,
slot/lane relationships and locked mutation basis. Detail projection additionally
calls `validate_task_review_storage` in that same snapshot; completion supplies
its Task ID for the same review validator. The default remains global for other
consumers, including the shared Task edit/write helpers. Task and review
adapters opt into local admission through every read and locked write; explicit
Runner Plan actions retain global admission throughout, including combined
verification or Contract changes. This internal boolean is not an authority token or public option;
neither local admission nor its snapshots may supply a global validation proof.
Review adapters supply their admitted Task ID through `review_task_id` when
opening a snapshot or revalidating a writer. Packet projection, retained hook
reads and event lookup validate the selected review storage inside their
existing snapshot once that Task is known. Schema/policy/location-only reads
remain structural; Runner audit consumers validate their consumed Runner rows
and required pending-attempt inventory. Integrated Core detail delegates selected
review validation to the Task projection; exact collected-registration inventory
instead validates the admitted Task and selected review storage directly, without
usage or report-only detail. Typed finalization completion uses the
same local boundary as ordinary completion. Source leases, native basis and
effect fences remain independently checked at their existing boundaries.

Maintenance opens metadata-only eligibility snapshots with local admission.
Evidence and Viewer captures read enabled/generation state first, then call
`validate_current_database` in the same snapshot only when a complete artifact
basis is needed. Their publication guards and metadata writers remain globally
admitted. Routine backup opts into local admission for its initial policy
and generation inventory read and failed/deferred outcome-only writes; the repository default, locked reconciliation,
copy validation and publication remain global. No eligibility snapshot or
generation comparison is reused as a complete-source proof.
The outcome writer rechecks its observed success/outcome tuple and complete
ordered generation metadata inventory in its own transaction. Publication and
recovery classification compose structural
admission with the exact-schema complete audit within the same read snapshot,
instead of repeating that audit through both admission entry points.

Task reads acquire all that Task's continuation generations
and use the retained-original composite primary key to fetch only those
consumers' captures. The same fetched inventory supplies slot counts and all
ancestor, metadata and consumption checks. Whole-store admission reads the
continuation inventory once, groups it by project/Task, and applies the same
complete validator to every Task; it does not repeat a whole-table acquisition
per Task. These values live only within the caller's existing snapshot, with
no cached validation across operations or change to schema or read permissions.
Operation-local review reads do not use or publish the
[resolver's optional global proof](setup-state-design.md#fixed-state-resolver).
Current binding and Task-specific reads belong to each fresh snapshot on every
platform; no graph rows or admission token survive between operations.
Selection validates the original Reference and provenance,
then derives heads from links, never time order. A matching pending continuation
blocks completion and suspends selected reassessments; unselected PASS is
retained. Resolution links refer to a Finding in that successor's predecessor
chain and an actually resolved Finding. Only that explicit typed relation
satisfies the same-generation fresh-review exception.

Migration requires the exact schema-28 physical fingerprint and admitted rows.
It uses the shared predecessor snapshot described under
[migration sequence](#migration-sequence), rejects name collisions
and unowned rebuilt-table attachments, rebuilds under the existing idle
connection/foreign-key protocol, inserts revision-one metadata and restores
owned objects, validates before and after the final marker, and commits once.
Every injected failure rolls back both schema and rows. Activation also updates
the object inventory, schema fingerprints and all compatible read consumers.

`completion_evidence_bundles.continuation_basis_json` is nullable TEXT with
valid JSON and a 16,777,216 UTF-8 byte bound. It is null for formats one/two and
non-null for source-schema-29 format three or source-schema-30/31/32 format four
when a continuation graph exists. Format four without a continuation keeps it
null. The existing immutable Bundle
guards protect this column. It stores the closed sanitized continuation graph
captured at sealing, not original review bodies or operational observations.
Projection reuses this frozen value; later continuation/resolution rows never
change an earlier sealed graph. Whole Bundle size still uses the existing
16,777,216-byte limit including every payload member and envelope.

Pure [graph validation](evidence-design.md#continuation-graph-validation) is
separate from source acquisition and frozen-to-native comparison in
`review_continuation_evidence_repository.py`. Its internal responsibility
checks share only call-local values; they acquire no connection, retain no
validation across reads, and do not change the caller's transaction ownership.

<a id="collected-answer-value-schemas"></a>

## Collected Answer Value Schemas

These are the current closed schemas introduced by schema 30 and retained by
schema 32, the public writer
and Evidence; the [review owner](review-completion-specification.md#collected-review-answers)
owns answer and operation behavior. Every listed key is mandatory, including
nullable keys; objects admit no extras. JSON integers are exact signed-64-bit
integers, never Boolean or float. UUIDs use the current canonical session/turn
validator; tool IDs retain prefix plus sixteen lowercase hexadecimal digits.
Host dispatch/message IDs contain 1..256 ASCII characters matching
`[A-Za-z0-9][A-Za-z0-9._:/+-]{0,255}`. New digests are `sha256:` plus 64 lowercase
hexadecimal digits, except existing continuation selection/basis digests which
retain their bare-hex domains and grammar. Canonical UTF-8 JSON admits no BOM,
non-finite value, float, lone surrogate, duplicate or unknown key. Envelope
nesting is at most sixteen containers. All typed strings receive the common
privacy check before semantic validation; errors never echo rejected values.
Text lengths count Unicode scalar values and byte offsets address exact UTF-8.

`CollectionBasis` is exactly:

```text
{version:1|2, preparation_id, packet_digest, request_digest,
 protocol:"review-answer-v1", attestation:"assigned-independent-review-v1",
 route:"manual"|"integrated", continuation, execution_basis}
```

`execution_basis` is the complete closed version-one continuation basis above,
bounded to 16,384 canonical UTF-8 bytes, with known execution and ownership and
capture-version-one current target. Manifest, authority and criterion IDs must
match the saved Packet. `continuation` is null or exactly
`{continuation_id,sequence,selection_digest}` using the existing admitted
selection; sequence is 1..64 and selection digest retains bare hex. The actual
`tg_review_preparation_` ID identifies the immutable preparation. Packet/request
digests hash their exact complete bytes; requests may differ by slot, but all
other basis fields must agree. Canonical basis bytes are at most 32,768.
Basis digest is SHA-256 over `taskgov-review-answer-basis-v1\0` plus the complete
canonical CollectionBasis.

Version one retains exact dispatch/turn collection. New issuance selects
version two for dedicated-reviewer first-valid-answer tracking. The version
is inside the immutable basis and its digest; reopening an older preparation
does not upgrade it. Both versions use the same five-field answer protocol.

Version-one `SlotBinding` is exactly:

```text
{basis_digest,slot,reviewer_key,dispatch_id,parent_session_id,
 reviewer_session_id,initial_turn_id,binding_source,assignment}
```

Slots are 1..8, distinct and contiguous in their preparation. Reviewer keys use
current normalized nonempty text up to 500 characters; both keys and actual
reviewer sessions are distinct. Parent/execution matches the basis, child
differs from parent, and the initial turn is the admitted actual dispatch turn.
`assignment` is exactly `{kind:"independent",review_profiles,review_lenses,
requested_model,requested_effort,requested_skill,context_relation}`. Profiles
and lenses retain the existing fixed-order sets and limits (four/eight).
Model is null or the existing 128-byte identifier. Effort is null or exactly
`none|minimal|low|medium|high|xhigh|max|ultra`. Skill is null or `{id,version}`
using current 128-byte identifier/64-byte version grammar. Context is null or
the existing context-relation code. Null means no recorded launch request;
none of these requested values proves an actual runtime fact.

Version-two `SlotBinding` is exactly:

```text
{basis_digest,slot,reviewer_key,parent_session_id,reviewer_session_id,
 binding_source,assignment,tracking}
```

The common identity/assignment fields keep the bounds above. `tracking` is
exactly `{kind,boundary,boundary_turn_id,dispatch_id}`. Initial kind uses
`kind="initial"`, `boundary="inclusive"`, the admitted sole own turn UUID,
and the actual initial dispatch ID. Continuation kind uses
`kind="continuation"`, `boundary="exclusive"`, the latest terminal own turn
observed before the additional request, and null dispatch ID. It records an
observation boundary, not a proven additional-request dispatch. The actual
adopted turn and message belong to the frozen SourceIdentity below.

The operational `RequestRound` may additionally hold `legacy_migration`, exactly
`{version:1,assignments:[{session_id,reviewer_key,origin_continuation_id}]}`.
Its one through eight assignments follow the current selected session order,
with distinct sessions and keys using the bounds above. Origin uses the native
continuation-ID grammar. A first explicit declaration points to its own fence;
later declarations preserve that same origin and key in the same preparation.
Every declaration is checked against its native selection and complete basis.
Absent declarations are omitted, not encoded as null; explicit empty or
noncanonical declarations reject. The existing 32,768-byte round limit and
physical schema remain unchanged. This records future dedication only;
terminal-turn observation remains in SlotBinding, and no legacy row is rewritten.

An observation source is exactly `{adapter:"codex-app-tools-review-answer",
version:1,operation}`. Operation is `dispatch_binding`, `answer_observation`,
`terminal_observation` or `correction_binding` at its respective field. Only the
implemented reviewed adapter assigns it; it is not authentication or caller
authority. Version two uses the same source shape, with
`version=2` and operations `reviewer_tracking`, `answer_observation` or
`terminal_observation`. A version-two binding requires `reviewer_tracking`;
it cannot be labelled `dispatch_binding`. Answer and terminal source versions
must agree with the collection basis. `SourceIdentity` is exactly
`{reviewer_session_id,turn_id,message_id}`, all non-null and exactly bound.

Native `OriginalRef` is exactly `{path,byte_length,digest,payload_start,
payload_length,payload_digest}`. Its public form removes only `path`. Path is a
prepared repository-relative ignored physical path under existing transport
grammar and limit. Byte length is 1..262144, payload start is nonnegative,
payload length positive and end is within original; both endpoints must be
UTF-8 scalar boundaries. Original digest hashes the complete delivered Unicode
answer encoded as UTF-8, preserving all whitespace/fence bytes. Payload range
selects the exact JSON object, excluding permitted surrounding whitespace and
sole `json` fence lines. LF/CRLF fences have no indentation or suffix. Payload
digest hashes those exact selected bytes; neither digest uses a domain prefix
or reserialized/normalized answer bytes.

Complete collected `Metadata` is exactly:

```text
{basis,binding,attempt,source_identity,original,answer_source,
 terminal:"completed",terminal_source,correction_binding}
```

The first two members use the closed types above; attempt is integer zero or
one. For version one, source identity matches binding's initial turn and has null
correction binding. Attempt one requires exactly `{intent_id,initial_turn_id,
correction_turn_id,source}`, with `tg_review_correction_` ID, distinct exactly
bound initial/correction turns, and correction source operation. Its source
identity is the correction turn. Answer/terminal source operations are exact.
Version two has exactly attempt zero and null correction binding. Its source
identity identifies the frozen completed answer from the assigned reviewer,
under the admitted tracking boundary; it does not assert an exact additional
dispatch-to-turn relation. New continuation metadata never rewrites a retained
version-one source, and mixed retained inventories preserve each source version.
Complete metadata is capped at 32,768 canonical UTF-8 bytes. Its physical seal
is SHA-256 over `taskgov-collected-review-metadata-v1\0` plus all metadata,
including the prepared path. It cannot validate a legacy sidecar arm.

The version-two operational record is exactly
`{version,basis,binding,attempts,correction,registration,tracking_state}`.
Version and basis version are two; binding is null or the version-two binding,
and correction is null. `tracking_state` is `watching|frozen|invalidated`.
`attempts` contains one entry with ordinal zero and the existing bounded
attempt fields: `turn_id`, `source_identity`, `observation`, `observed_digest`,
`terminal`, `terminal_source`, `publication`, and `failure`.
While watching, rejected observations can replace only these bounded diagnostic
facts; digest and publication stay null. Freezing requires a complete valid
answer, completed terminal state and an atomic publication intent, fixing its
source identity, digest and original reference. Publication then follows
`intent|saved|unknown`; registration retains its existing monotone tuple.
Watching may become frozen or invalidated, and frozen may become invalidated.
An invalidated record cannot change again. The physical operational schema
remains version one, and old record versions are never rewritten by reading.

After explicit rejected-answer replacement, a healthy version-two source may
add `retired_registrations`, a nonempty list of at most 64 exact
`{continuation_id,registration}` entries. The continuation identifies the
admitted successor; the registration is the unchanged previous dispatched or
unknown tuple with null Receipt. Entries only append, continuation IDs and
submission digests are distinct, and the new current tuple is reserved directly
as dispatched with a different digest. No old attempt returns to not_started.
Absent lists remain omitted, preserving old canonical bytes.

The operational `RequestRound` may carry `rejected_answer_replacement` with
exactly `{version:1,source_continuation_id,sources}`. The nullable context is the
retired consumer round. Sources are 1..8 distinct session-ordered objects with
exactly `source_continuation_id`, `slot`, `reviewer_session_id`, `turn_id`,
`message_id`, `registration`, and `rejection`. The source context is nullable;
identity uses the existing slot/session/message grammar. Registration is its
unchanged not_started/dispatched/unknown tuple without a Receipt. Rejection is
null for a healthy retained source or exactly `{field,verdict,severities}`:
field is `summary|findings[].summary`, verdict retains the three answer choices,
and severities retain at most 64 `high|medium|low` values. At least one source
is rejected. No original digest or body is added. This value is immutable from
the fence, is incompatible with legacy migration, and is omitted when absent.
The existing record bound and physical schema remain unchanged.

An integrated successor round may add `replaced_registration_round`, the
immediately prior zero-based sequence. That old round remains verbatim with
dispatching registration and untouched commit/completion. The field is omitted
when absent and permits no general unknown-effect reset. The replacement's
admission and fresh absence requirements belong to the
[review contract](review-completion-specification.md#rejected-unregistered-answer-replacement).

A collected envelope capture is exactly `{kind:"collected",
consumer_continuation_id,consumer_slot,source_preparation_id,
source_continuation_id,source_slot,session_id,source_basis_digest,
original_digest,metadata_digest}`. Only source continuation may be null; slots
are 1..8. Native storage retains this identity plus unchanged complete source
metadata and one-time nullable result Receipt link. Consumer slot is the new
dispatch slot: selected reviewers occupy the leading slots, followed by the
combined legacy and collected retained originals in session-ID order, without
collisions or gaps. Source slot and every source identity remain the original values.
An existing legacy capture retains its old four keys and old hash domain.
The [late-initial submission arm](review-completion-specification.md#collected-review-answers)
appends uncaptured initial sources after this frozen captured subset. Those
source-use references are transport values, not native captures; they add no
table, capture rank or stored-metadata variant.

Public `capture_identity` removes only `metadata_digest` from that collected
capture. Public `source_metadata` removes only `original.path` from complete
Metadata. A frozen public row is exactly `{capture_identity,source_metadata,
evidence_digest,result_receipt_id}`; its full canonical size is at most 32,768
UTF-8 bytes. Evidence digest is SHA-256 over
`taskgov-collected-review-capture-evidence-v1\0` plus exactly
`{capture_identity,source_metadata}`, excluding the separately validated nullable
Receipt link. No path placeholder/hash, original body or provider envelope is
included. Rows order by consumer continuation/session in unsigned UTF-8 order.
The distinct native/public seals and deterministic native projection are stored
atomically; readers cannot supply independent public metadata as proof.

## Migration Sequence

`schema_preservation.py` owns the read-only predecessor snapshot and comparison
used by migrations 29, 30, 31 and 32. Each caller fixes its exact allowed object
changes before the delta. The helper captures all predecessor tables, typed
column/row-identity projections, unaffected objects and the complete
`(rowid,version,name,applied_at)` migration history. It performs no DDL, marker
insertion, admission, commit or rollback; each migrator retains those duties.
Both after the delta and after marker insertion, it compares the same frozen
old basis, permitting only the exact new marker with the caller's single chosen
timestamp. New schema-29 columns such as `judgment_revision` and
`continuation_basis_json` do not change that old projection. Exact owned-schema
and domain validation still checks intentional additions; schema 29 retains its
revision-one/no-predecessor judgment check.

The snapshot adds each table's actual rowid to the same typed value projection,
detecting identity changes and identity/value reassociations. Unshadowed `rowid`,
`_rowid_` or `oid` names are selected using complete column metadata; when all are
shadowed, only a proven `INTEGER PRIMARY KEY` alias is used. An indexed primary
key, including column-level `PRIMARY KEY DESC`, is not a rowid alias.
`WITHOUT ROWID` tables retain their named-column projection; unaddressable rowid
identities fail before the delta. Classification uses `index_info(table)` metadata
(SQLite 3.30 or later); missing identity capabilities fail closed. The inventory
excludes only the literal reserved `sqlite_` prefix, and includes `sqlite_sequence`
when present; engine optimizer statistics are not business-row projections.
No prefix wildcard may omit a legal user table such as `sqliteXkeep`.

Schema 30 is owned by `schema_review_answers.py`; the exact code-defined owned
DDL fingerprint covers the Receipt/Evidence Reference/Bundle rebuilds, coupled
continuation/publication guards and five new relations. The migration uses the
same admitted idle connection, foreign-key/legacy-alter protocol and before/
after predecessor-projection/object checks as schema 29, including the shared
post-marker comparison. Marker insertion is last within the single transaction.
All exits restore connection flags.

`review_receipt_provenance_v2` has primary/FK Receipt ID, unique provenance ID,
bounded canonical `provenance_json` (262,144 bytes) and its contextual digest.
`review_collection_sources` has one Receipt primary/FK, preparation/slot/attempt,
reviewer-session/turn/message, bounded canonical CollectionBasis JSON and
basis/original/payload/submission digests;
both the complete source tuple and session/turn/message are unique. Its deferred
preparation/submission FK requires the dispatch inventory in the same commit.
`review_collection_dispatches` has preparation/submission composite primary key,
project/Task and canonical inventory JSON capped at 32,768 UTF-8 bytes.
`review_collection_dispatch_entries` has the dispatch/slot primary key, unique
dispatch/Receipt, Receipt FK and exact canonical entry JSON. These tables are
immutable. The repository compares both inventories and all immutable source
and Receipt/session relations; a missing subset is conflict, not a prefix.

`review_collected_captures` has consumer continuation/session primary key,
nullable source continuation, bounded complete native metadata JSON, native
metadata digest, bounded immutable path-free projection JSON, and nullable
unique Receipt FK. Only that Receipt link may publish once. The frozen projection
stores a null link initially; readers substitute the actual native link without
changing its immutable source metadata or evidence digest. Capture construction
derives both seals from the same validated metadata. `review_answer_repository.py`
owns these reads/writes, exact inventory reconciliation, deterministic projection
comparison and source/capture validation; it performs no file/host I/O and never
commits the caller's transaction. Operational collection storage is separate.

Receipt version two uses its separate relation; the former v1-only Receipt FK
is replaced by exact union admission, without creating a v1 row for v2. Legacy
v1 code rows and bytes remain unchanged. Read admission rejects orphan/multiple
arms, invalid kind/version/source/target/digest matrices, missing Findings or
normalization mismatches. Bundle 4 uses source 30, 31 or 32 and the existing nullable
`continuation_basis_json` for graph 2; the coupled native-cycle guard requires
null graph with ordinary gate basis one and a graph with continuation basis two.

Schema 31 is owned by `schema_criterion_link_collector.py`, with migration name
`criterion_link_collector` and its own exact owned-DDL fingerprint. It admits
the unchanged schema-30 fingerprint and rows before rebuilding only
`criterion_evidence_links` and `completion_evidence_bundles`. The link producer
CHECK adds `taskgov_answer_collector`; its Reference-equality guard is retained.
The Bundle discriminator and `trg_task_completion_cycles_evidence_basis_insert`
admit native source 31/format four with the same gate-basis/nullable-graph matrix.
No columns, tables, indexes or triggers are added: schema 31's inventory stays
50 tables, 47 explicit indexes and 101 triggers.

This forward migration uses the admitted idle connection and existing
foreign-key/legacy-alter protocol, with one `BEGIN IMMEDIATE`, exact predecessor
column/rowid and unaffected-object comparisons, marker-last insertion, and
validation before commit through the same shared preservation helper.
All exits restore connection flags; failures roll
back the complete delta. Unknown attached DDL or conflicting temporary names
fail closed. Existing links, original provenance and sealed Bundle bytes remain
unchanged. Source-30 DDL, fingerprints and validators remain exact; current
schema-31 reentry validates without repairing or replaying the migration.
Setup, recovery, backup, relocation, Viewer and Evidence readers dispatch by
the actual source version. Bundle format four, index three and their digest
domains remain unchanged.

`schema_migrations` is contiguous, named, and validated with required objects
and known later-version markers. Only explicit setup invokes migration. Other
commands never create parents, create a database, stamp missing history, or
perform a reverse migration. Older binaries reject newer schemas.

Current sequential migrations are:

| Version | Name / owned state |
| --- | --- |
| 1 | initial task, event, tool-event, and project metadata schema |
| 2 | compatibility completion-commit requirement/hash projection |
| 3 | `paused` status and exact pause-reason matrix |
| 4 | typed completion evidence |
| 5 | review target, receipt, and finding evidence |
| 6 | Git-snapshot base and receipt target expansion |
| 7 | local handoff outbox |
| 8 | immutable Task Contract revisions and current pointer |
| 9 | Effort Advisory activity generations and bases |
| 10 | one-way project maintenance and backup/Viewer outcome state |
| 11 | managed backup generation inventory |
| 12 | append-only typed checkpoints |
| 13 | Viewer source/render generation state |
| 14 | stable identity and append-only path-binding history |
| 15 | immutable completion-cycle history and internal event linkage |
| 16 | marker-only native completion-capture activation |
| 17 | immutable Verification Receipts and completion-cycle verification basis |
| 18 | authority/criterion capture, Review provenance, target manifests, Evidence References, and Verification subjects |
| 19 | native completion Bundles, criterion links/Finding snapshots, and Evidence JSON projection state |
| 20 | verification Runner shadow storage and Bundle-v2 null-Runner tagged union |
| 21 | verification Runner gate-basis tags using the existing schema-v20 structures |
| 22 | retired Analyzer reservation cleanup in the existing Evidence/Bundle tables |
| 23 | `verification_declaration`: Task and completion-cycle waiver reasons |
| 24 | `task_session_ownership`: current ownership and immutable execution/transition/cycle relations |
| 25 | `review_receipt_sessions`: immutable actual-reviewer/execution binding |
| 26 | `review_pending_session_slot`: combined slot acquisition guards and transition policy version |
| 27 | `review_pending_reacquisition_guard`: forward correction of legacy pending reacquisition |
| 28 | `task_event_note_bodies`: nullable full-body retention on Task events |
| 29 | `review_continuations`: successor judgments, explicit reassessment resolution and frozen continuation evidence |
| 30 | `collected_review_answers`: v2 provenance/source, immutable dispatch inventory and typed collected captures |
| 31 | `criterion_link_collector`: collected producer on criterion links and native source-31 Bundle/cycle discriminators |
| 32 | `session_slot_lookup_index`: non-unique combined actor/project lookup and native source-32 Bundle/cycle discriminators |

Every migration is ordered, idempotent on reentry, transactional, and
rollback-tested. Reentry validates rather than synthesizing missing data.
Table rebuilds preserve foreign keys and IDs and restore foreign-key
enforcement in a `finally` path. Migration validation uses
`PRAGMA quick_check`, `PRAGMA foreign_key_check`, exact row/object
preservation, and the sanitized realistic 12-task/191-event fixture with nine
historical completion hashes and representative review, Contract, handoff,
checkpoint, maintenance, identity, and completion traces. The sole current
exception is migration 20's Bundle-rebuild retirement of the
unsupported attached residue defined below; it changes no other migration.

The fixed-state setup migrator accepts complete source schemas v1-v31 and
treats v32 as current. Legacy `state/projects` discovery is intentionally
narrower: v1-v13 plus the explicit schema-v14 legacy-layout transition.
Viewer compatibility is independent and accepts source schemas v5-v32.
Incomplete history, a missing required object/row, a later marker, too-new
state, unsupported layout, foreign identity, or corrupt integrity fails closed.

<a id="session-ownership-migration"></a>

### Session Ownership Migration

`schema_task_ownership.py` owns the exact v24 DDL and predecessor migration.
`storage.py` composes the ordered 23→24 tail, later-object recognition, global
admission, native source version and snapshot proof. Backup, resolver, relocation
and Viewer use the same supported version, not a separate weaker reader.
The fixed owned-schema fingerprint is checked together with expected object SQL,
marker-last preservation, foreign keys and quick check. Migration compares all
predecessor business columns and unrelated objects before and after marker insert,
and verifies empty new history and unknown/none ownership without semantic backfill.
Its Bundle rebuild and temporary-name checks follow the existing transaction and
PRAGMA restoration protocol; injected copy/marker failures roll back all additions.

`task_ownership` is keyed by Task and references one same-Task execution when known.
The active partial index is `(project_id, owner_session_id) WHERE state='owned'`.
`task_executions` stores a random ID and start time. `task_owner_transitions` stores
only Task/execution/generation, source/destination status, resulting ownership state,
actor UUID, optional sanitized recovery reason and timestamp. `task_execution_cycles`
links one exact native cycle to its unique execution; its insert guard checks the
cycle's project/Task and immutable triggers prevent rewriting the relation.
The shared ownership row validator also rejects missing links for native Bundle
sources 24 through 32 in ordinary global and recovery admission. Earlier retained
completion history remains valid without an invented execution/cycle link.
No prompt, response body, environment, cost, session liveness or caller-specific
flags are retained. This schema does not create the later numerical usage store
or review-session binding tables.

<a id="conditional-reviewer-binding-migration"></a>

### Reviewer Binding Migration

`schema_review_sessions.py` owns the setup-only 24→25 migration, exact DDL and
fingerprint. Schema 25 remains a supported predecessor. Migration retains the existing short-writer, marker-last
snapshot proof and PRAGMA-restoration mechanics. It adds the empty binding
relation and rebuilds only the Bundle source discriminator and coupled native
cycle guard, preserving all old columns, rows and unrelated objects.

`review_receipt_sessions` is keyed by Receipt ID and references an execution.
Its fixed columns are actual session UUID, execution ID, `direct|handoff`
source and nullable original digest (required only for handoff). An insertion
guard checks matching Receipt/execution project and Task; immutable triggers
reject updates/deletes. Repository validation additionally checks the exact
recorded Evidence Reference, full target and same-session aliases. Binding is
inserted in the Receipt's existing transaction, never an attached numerical DB.

Storage composes the ordered tail and exact inventory. Backup, recovery,
Evidence Bundle/index, Viewer and relocation codec recognize the current
source version without weakening normal database-version admission. Core
completion keeps the schema-24 execution/cycle link and format-2 Evidence
encoding; reviewer identity is not added to the sealed payload. Old readers
reject schema 25 rather than dropping its registration constraints.

<a id="combined-session-slot-migration"></a>

### Combined Session Slot Migration

`schema_session_slot.py` owns Setup-only 25→26 migration and its exact DDL/fingerprint.
The public runtime ceiling follows the current supported schema in the
[database specification](database-specification.md#initialization-and-supported-schemas).
Schema 26 retains the original ownership states
and active partial index. New insert/acquisition triggers check the combined
owner/completion-owner session/project slot. A retained legacy overlap is not
rewritten or arbitrarily assigned: only continuations/releases of its existing
holdings are possible until explicit recovery removes the conflict. The
repository applies the same combined check inside its existing writer.
The migration adds `policy_version INTEGER NOT NULL DEFAULT 0` constrained to
zero/one on immutable `task_owner_transitions`; an insert guard requires one
for new transitions. The numerical reader uses that structural version to keep
old interval meanings while continuing new review-pending intervals. No
numerical schema change, manual measurement operation or new completion gate
is introduced. Setup preserves all predecessor column values, owners, statuses,
cycle links and sealed Bundle bytes, with marker-last rollback and reentry
validation. Old binaries reject schema 26.

`schema_session_slot_reacquisition.py` owns the explicit 26→27 forward repair;
it never redefines the applied schema-26 fingerprint. It replaces only the
combined update guard plus the Bundle source discriminator and native-cycle guard.
The update guard also treats `completion_only`→`owned` as an acquisition, matching
the repository when legacy holdings overlap. It retains every predecessor row,
unrelated object and Bundle payload, checking those projections before and after
the final migration marker. DDL/copy/marker failures roll back to exact schema 26;
reentry validates exact schema 27 without repair. Both supported source definitions
remain independently validated. Numerical schema and policy-zero/one meanings do
not change, and retained overlaps still require explicit release/completion.

#### Combined Slot Index Migration

`schema_session_slot_index.py` owns Setup-only 31→32 migration and its exact
owned fingerprint. It creates non-unique `idx_task_ownership_held_actor` on
`(project_id, coalesce(owner_session_id, completion_session_id))` with the
same held-state partial predicate as `_free_slot` and both retained acquisition
triggers. The service and INSERT/UPDATE/reacquisition EXISTS searches therefore
use one actor/project range. Release makes no new slot query. The old active
unique index and all ownership trigger SQL stay byte-exact; legacy overlap is
neither removed nor admitted as a new acquisition.

The migration rebuilds only the Bundle source discriminator and native-cycle
guard to admit source 32/format four, then creates the lookup index. It uses the
shared predecessor row/identity/object/history proof before the delta and after
the final marker. Destination-name collisions fail before the delta; failed
DDL, copy, index or marker work rolls back all changes. Current reentry validates
without repairing, and source-31 definitions remain unchanged. Backup, recovery,
relocation, Viewer, Evidence and the independent reader use actual source 32
with the unchanged format and domain contracts.

<a id="task-event-note-body-migration"></a>

### Task Event Note Body Migration

`schema_task_event_notes.py` owns explicit 27→28 migration and exact owned SQL
fingerprint. `task_events.note_body` is nullable TEXT, constrained to at most
2,000 characters. Three triggers restrict body-bearing inserts to the parent
Task/project and the three editable event types, freeze the body and its
event/Task/project/type binding, and reject deletion of retained bodies. No
extra event or table is created. Null remains an explicit unretained-body value
for predecessor events; the migrator never parses their summaries.

The Bundle source discriminator widens to 28 and its native-cycle guard selects
28; format-2 payload members and digests remain unchanged. All predecessor
column projections and unrelated objects are compared before and after the
marker-last migration, with DDL/copy/marker rollback and read-only reentry.
Storage, Evidence, Viewer, backup/recovery, relocation and Runner consumers use
the same source ceiling. Stored note validation remains structural even for
recovery; it is not the Task-local verification rejection exception. The
numerical schema and frozen predecessor schema definitions remain unchanged.

## Numerical Collection Persistence

`usage_repository.py` owns independent schema 1 (`response_collection`), its
exact owned DDL and shared numerical connection/project/binding admission.
It contains nine tables: `usage_migrations`, `usage_meta`, `usage_sessions`,
`usage_sources`, `usage_incarnations`, `usage_responses`, `usage_observations`,
`usage_conflicts`, and `usage_diagnostics`. Composite primary/foreign keys
connect source incarnations and response observations. No numerical table is
attached to the main database or consulted by a core permission/quality gate.

Setup creates a sibling temporary store, commits its complete schema and
binding, closes/flushes it and uses the existing no-replace publisher. Failure
before publication leaves the live destination absent and removes only the
attempt's private temporary. Current-state reentry validates without repair;
an incompatible or newer store remains intact and unavailable. Ordinary access
uses existing rollback-journal preflight, query-only read transactions or a
short `BEGIN IMMEDIATE`; no log reads occur under the writer. Setup quick/FK
checks and exact schema/binding checks apply independently of main admission.

`usage_values.py` validates the closed numerical representation.
`usage_adapter.py` retains the full-prefix baseline adapter and shared closed
projection: an explicit session
header supplies provider; matching turn context supplies model/effort; each
`token_usage_record` supplies exact thread/turn/response identity and counts.
Response arrival never advances or rewinds the explicit session/turn context
used to associate legacy records, so delayed observations cannot hide a newer
turn's missing coverage.
The adapter reads no unrelated files and has no SQLite or discovery code.
The current `usage_incremental_adapter.py` resumes separate ingest/audit contexts.
One line remains bounded to 8 MiB; a slice targets 256 KiB and 512 records and
may exceed the byte allowance by one bounded record plus the validated header.
Oversized records drain in 64 KiB chunks with a separate byte position; no body
is saved and the complete cursor does not move until newline. A monotonic
deadline stops further work at record/chunk boundaries. These are work bounds,
not acceptance truncation or an all-environment latency promise.

The resumable hash chain includes only normalized allowlisted metadata/fixed
markers, structural turn projections and byte boundaries. Unknown bodies affect
only record length. Ingest seeks directly to its committed cursor. Audit keeps
its own model/context state and fixed goal offset/digest; constant append cannot
extend that goal. A completed cycle restarts from the header on a later event.
Mismatch marks ingest for new-incarnation replay. Source-object/size/mtime
revalidation rejects a concurrent change during a slice; it does not claim to
detect all between-event edits immediately. Persistent
`prefix_verification_deferred` makes that detection window explicit.

The repository locks, compares the full previous cursor, persists the source
incarnation, inserts new unique responses or conflict keys and observation
links, then updates diagnostics and cursor in the same transaction. Any failure
rolls back the whole batch. Stored cursor types and shape are validated before
reading the source; corruption returns numerical unavailability. Cursor conflicts
return a fixed diagnostic; the caller does not loop or rerun a Task operation.
Internal lifecycle registration
captures a typed actual caller and binds only its exact source identity.
Per-source modern/legacy turn coverage is retained atomically with the cursor;
delayed modern observations resolve only their matching legacy turn. The writer
updates `legacy_usage` only when needed; other diagnostics/history remain.

`UsageIncrementalRepository` adds numerical schema 5 (`incremental_collection`):
`usage_scan_state`, `usage_scan_models`, `usage_scan_coverage`, `usage_work_order`,
`usage_discovery`, `usage_discovered`, `usage_dirty_turns`, and
`usage_component_basis`, plus a response-turn index and insert/conflict dirty
triggers. Continuations contain only closed context, incarnation, revision and
audit goal metadata. Directory inventory retains numeric date parts, entry
ordinals and an attempt watermark for partial-page continuation, never filenames
or log bodies. Explicit Setup migrates any exact
schema 1–4 in one transaction, preserving observations and immutable Evidence;
ordinary access fails closed until then. First collection replays an old cursor
whose continuation is absent. Scan CAS, context/coverage, response/conflict
observations, dirty marks and cursor commit together or all roll back.
Model/legacy contexts are disposable continuation caches: a new incarnation or
audit cycle clears only the obsolete lane context. Adopted dirty marks and
retired component bases can be deleted because their effect is already captured
in immutable snapshots; no response, observation, incarnation, snapshot, member,
cycle link or supersession history is deleted.

`usage_incremental_projection.py` reevaluates structural interval geometry and
diagnostics using the admitted core and numerical snapshot. A component basis
captures its execution/turn/gap set and unresolved-operation state. A changed
basis or dirty response turn loads and sums only that component's original
responses; unchanged components reuse validated snapshots. New graph geometry
and immutable predecessor overlap handle splits/merges without summing old
totals. Dirty marks and bases are adopted with snapshots, cycle links and capture
fingerprint. Core restore or delayed boundaries/reviewer/receipt bindings cannot
reuse a different geometry. The structural pass is still global; current
snapshot validation still reads membership/counters. Historical Evidence and
file integrity use the resumable publication sweep below. Test work counters
distinguish aggregation from these integrity operations.

Capture uses the transaction-local `SnapshotRead` described below to load
current membership and unique originals in bulk. Execution, response and
partition indexes select immutable predecessors without scanning every current
snapshot for each component. Legacy-format, empty and retired partitions keep
their existing overlap rules; this changes lookup work, not attribution.

`usage_collection.py` composes those boundaries and returns unknown on numerical
failure without discarding readable prior observations. Its explicit-setup
adapter runs after core setup, reports a separate outcome and never changes
core success. Host integration belongs to the
[lifecycle adapter](setup-state-design.md#lifecycle-usage-hook), not this module;
no Task operation, completion callback or host-settings writer is added here.

<a id="conditional-turn-attribution-structure"></a>

### Turn Attribution Structure

`usage_turn_adapter.py` projects only host `task_started` metadata and the
allowlisted fields of successful Task acknowledgements from structured tool
results. It accepts the observed `input_text`/text blocks and exec output
container, not command arguments or general natural-language relations.
For registration it accepts full bodies or the closed same-response Task/event
references. Only the fixed selected Task path or canonical selected-event index
is resolved, after ready preparation and matching identity checks. Batch entries
resolve against their one original response context before the same operation
projection. Unknown paths, malformed indices and mismatches yield no boundary;
no arbitrary pointer traversal, additional read or context-body retention occurs.
The shared `project_body` returns a transient `BodyProjection` containing
validated observations, a separate unreadable-evidence flag, and terminal
qualification from validated helper metadata or an admitted failed command.
Each registration
entry validates independently against the original acknowledgement context.
The retained turn-only caller consumes valid observations without using them
as lifetime proof. It invokes the response adapter's complete `objects` parser
at the record boundary, without a second decoder or child traversal; the local
import keeps the tracker/semantic-projector dependency out of module loading.
Its sanitized adapter prefix is `codex-task-turn-v3`, so
older projections are rechecked through existing bounded source replay.
`usage_adapter.py` includes this projection only in attribution mode. The
sanitized prefix version distinguishes the two adapters, so switching mode
replays old numerical observations idempotently rather than losing boundaries.
Turn observations, response observations and source cursor commit atomically.

`UsageAttributionRepository` adds `usage_turns`,
`usage_turn_conflicts`, `usage_operation_turns`, `usage_operation_conflicts`,
`usage_review_boundaries` and `usage_review_receipt_turns`
to numerical schema 1. Explicit initialization migrates the migration-marker
table and adds the new relations in one short transaction, recording version 2
last. No ordinary read or collector migrates. The setup factory selects schema 7
for the current package. Preview returns `migration_required` and planned `usage_migrate` for
an exact schema-1 through schema-6 store; explicit setup returns `migrated` and that completed
write on success. Fresh-store creation keeps `usage_initialize`. Failure remains
a separate numerical outcome, not rollback or rejection of core setup.

Core read-side resolution uses the exact returned event ID and the transition's
project/Task/generation/status/actor tuple. Equal creation time is only an
additional consistency check, never a proximity selector. Missing core rows
after restore remain unresolved. The numerical store neither supplies core
state acceptance nor bypasses existing selected/global validation.

`usage_attribution.py` builds inclusive intervals from admitted append-order
core transitions. It unions turn and response membership before summing, builds
components over ownership executions, and separately projects cumulative Task
completion periods. It preserves a Task's pre-completion intervals across
execution changes without treating permanent Task IDs as graph edges. Its
current output is observed/pending; immutable cycle-linked publication uses
the repository below and the optional lifecycle adapter invokes it.
`usage_review_attribution.py` attaches reviewer intervals only to committed
core identity/target evidence. A reviewed execution and the reviewer's own
active execution can share the same turn; an unrelated parent's submission
turn cannot stand in for the reviewer's read/save boundary. Legacy unbound
Receipts add no inferred participation. Missing start history counts only a
known save/direct-registration turn with explicit partial coverage.

Current response attribution additionally reads collected-answer endpoints
through the repository boundary. The native reader validates the committed
Receipt's provenance, collection source and session/target relationship and
returns only typed identity and turn metadata; it never exposes an answer body
to numerical storage. `usage_review_attribution.py` consumes these endpoints as
a separate input, rather than synthesizing a legacy save observation. A native
collected Receipt uses its own source endpoint, while legacy handoff/direct
Receipts keep their validated save/registration sources. Unregistered captures
and mismatched relations cannot supply a boundary.

`UsageResponseRepository` includes the validated endpoint projection, its version
and `conservative-legacy-review-intervals-v1` in the capture basis before using
the interval and partition calculation. Source admission and endpoint ordering
remain distinct: a valid
native turn without its observed ordering metadata does not establish a full
interval. Keep the source identity in the validated input and capture basis,
but project a closed unknown end and retain a known read as the start, with an
unknown-boundary diagnostic. Both coverage consumers then preserve only the
definite read turn until safe endpoint ordering is available. Current review
boundary selection uses the inclusive projector's shared safe turn-order calculation,
including same-session collisions between distinct turn IDs. Inspect the full
observed turn set before excluding stored conflicts, so an excluded turn cannot
hide its collision with another boundary. The store retains only a turn's first
start plus its conflict identity. Union stored conflict keys with same-ID start
disagreements in the input and disable review range inference for their entire
reviewer session; do not judge an unknown alternate timestamp by the retained
value's distance from the boundaries. For that fallback only, remove conflicted
IDs before calculating safe read candidates. Keep an eligible matching read in
an explicit identity-only interval, with a closed unknown end. The shared
coverage function counts precisely that read key without expanding through
equal timestamps or rechecking it against a conflicted peer's retained clock.
An own-ID conflict still excludes the read, as do equal starts among remaining
non-conflicted candidates. No eligible read means no membership. Both consumers
use this same identity-only path. The response consumer marks the covered keys
partial, independent of whether discarded peer timestamps are present in its
ordering input. Full, sliced and restarted collection and different arrival
orders therefore preserve the same definite read and its partial assignment.
The response repository opts legacy handoff/direct Receipts into this ordering
policy with `conservative_legacy=True`; retained turn-only callers leave it
disabled. For incomplete legacy intervals, an identity-only key also preserves
partial assignment for a missing endpoint, an endpoint ordered before every
eligible read, or an otherwise safe endpoint with no read. Reversed endpoints
retain the earliest safe read and discard the end; absent safe keys add nothing.
Ordinary ordered intervals, other sessions and native collection keep their
existing meaning. The capture-basis version prevents adopting an old capture as
current until ordinary numerical refresh, without changing the response parser.
Changed geometry uses existing component invalidation and immutable
snapshot successors; old numerical readers and stored history retain their
meaning. The integration adds no persisted table, host operation or core write.

The response repository's refresh capture obtains one immutable review-input
value containing the validated Receipt/session/target relationships and native
endpoints. It passes this value explicitly to capture-basis and attribution
calculation within the same admitted core read transaction. The answer repository
owns endpoint validation; numerical code receives no answer body. A base capture
hook preserves the original refresh transaction and snapshot adoption, and the
publication repository forwards the input while retaining its additional basis
checks. The value is not stored on a repository or connection and has no durable
cache. Standalone reads and the later publication guard still retrieve fresh
input, so later registration, changed provenance and invalid Evidence are
reconsidered rather than hidden by an earlier successful validation.
The basis reader uses the same complete review-input reader when no capture-local
value is supplied. It validates direct and legacy handoff bindings as well as
native collected endpoints; endpoint-only validation cannot stand in for the
Receipt-to-Reference and execution/session checks required by the basis.

### Response Relation And Partition Structure

`usage_response_adapter.py` owns closed call/result/response metadata and
bounded continuation (128 retained call/slot positions in total, 128 handles
and 128 retired-handle entries,
262,144 bytes). `usage_response_attribution.py` owns response ordering and
whole-response start/stop/resume/completion partitions. `usage_response_repository.py`
owns numerical schema 6, core-reference resolution,
partition snapshots and read projections. Neither parser nor classifier uses
SQLite; SQL stays in the repository boundary.

Schema 6 adds `usage_response_positions`, `usage_response_boundaries` and
`usage_response_context`. Rows retain only source/incarnation identities,
thread/turn/provider/response IDs, call ID, record order, closed command/method,
and validated operation identities. Boundary digests hash only that closed
metadata. No command, argument, warning, private body or body digest is saved.
Contexts are independent for ingest/audit and commit under the same source
cursor and continuation CAS as counters, coverage, relations and dirty marks.
Unknown/foreign records break pending proofs without dropping numbers.
Capacity exhaustion preserves one bounded earliest lost-origin frame in the
continuation and projects its possible range as unknown after tracked calls
drain, including after restart. It does not create an origin response, cover
earlier independently confirmed work, or erase observed counters. The frame
is retained until source replay can rebuild the trace; it is not cleared by a
result for a call whose lifetime was lost. Opaque/unreadable transport retains
its unresolved frame until a supported terminal result is observed; opaque
stdout inside a positively terminated process envelope is not such transport.
Pending calls retain explicit unknown response coverage until their result can
be validated. Historical qualified event/call origins are compared across
incarnations; a replacement cannot silently relabel an old success. Candidate
read acknowledgements require the essential public selection/list/show shape,
not merely a known command name with an arbitrary data object.
`usage_response_adapter.py` classifies ordinary input as confirmed execution,
confirmed literal poll, or unknown. `_call_input` never treats a missing poll
match as execution proof. `usage_batch_adapter.py` shares its bounded literal
reader and exec-field allowlist with positive sole-execution admission; direct
JSON objects and the sole observed `text(await tools.exec_command({...}));`
wrapper qualify. A shared `strip_exec_pragma` removes only the bounded LF/CRLF
pragma. Its body excludes every JavaScript line terminator (LF, CR, U+2028,
U+2029), so it cannot swallow executable statements after a comment ends.
Sole-execution, sole-poll and batch admission all use this same function.
Unknown frames keep success observations and mark response
correspondence ambiguous, including explicit response metadata and inherited
handles. No additional member spellings or general JavaScript parser is added.
`usage_batch_adapter.py` also parses only the observed whole indexed batch grammar,
without evaluating JavaScript. It returns ordered tool kinds and canonical
literal poll handles; command text, argument values and store keys are discarded.
It retains output indices before the ordinary transport parser unwraps results,
using the same closed text/header parser and eight-level/2 MiB bounds. Routing
admits only outer script wrappers: process metadata objects are
not routing containers, and routing disables process-header stdout decoding.
This boundary precedes removal of the enclosing transport context. Each result
retains an unproven-index marker during semantic unwrapping if a further indexed
wrapper occurs below the admitted routing boundary. Before handle registration,
that marker makes the corresponding frame ambiguous; independent operation
observations survive, but stdout cannot create a response origin or handle.
The same rule applies to ordinary calls and polls without an admitted index.
Each routed result
carries its consumed transport depth transiently into value parsing and handle
extraction; it is not persisted and the indexed boundary does not reset it.
The rejection guard treats every executable `Promise` identifier reference,
unknown identifier escape and dynamic template as a batch candidate. Known
quoted/comment/regex tokens are inert; a validated sole-literal poll still
wins. This guard does not evaluate aliases or computed global properties and
does not expand the positively admitted grammar. No text is evaluated.
Line-comment scanning recognizes LF, CR, U+2028 and U+2029. Regex skipping
requires start-of-input or a typed punctuation token that cannot end an
expression; token values alone and keyword names never grant that permission.
Plus/minus and other uncertain slash contexts stop with an unknown candidate.
An unclosed regex or a line terminator inside it, including after an escape,
also stays unknown. This deliberately avoids implementing general JavaScript
lexical-goal or postfix/property parsing.
Results are matched by index, with malformed or duplicate indices rejected before any
slot is processed. Independent missing/rejected/opaque slots remain pending.
The batch's unique-key decoder mode follows every admitted fulfilled-value
text/container traversal, including header validation. Its ambiguity signal
is not converted into an opaque-block marker. Preflight every routed fulfilled
value, even a resolved slot, before registering handles or emitting observations;
one duplicate rejects that whole output without confirming a slot or changing
handles. First-output bracket closure is independent and still persists.
A sole poll inheriting a non-null original slot uses the same preflight before
removing its pending call or changing the handle. Ordinary non-batch origins
and opaque rejected reasons retain their prior rules.
All response parsing returns leaf bodies with transport output/content removed after
checking those children at the inherited depth, including mixed acknowledgement
and transport objects. The legacy turn projector receives only these validated
leaves and cannot re-decode discarded children under weaker or renewed bounds.
Continuation version 4 retains positional batch frames, each frame's original
slot index and a required Boolean `bracket_closed`. Close matching ordinary
frames and all unresolved batch slots before parsing the first host output,
including invalid turn metadata; the latter also invalidates their proof.
Only open fresh frames accept numerical bracket candidates. Explicit origins
start closed; live handles, inherited polls and lost-origin markers stay
closed. Poll frames inherit the original call/slot/turn/order/response;
fresh exec frames retain their batch call and position. Collision checks examine
all returned handles before publishing any slot's observations and invalidate
all retained aliases. Pending projection, interruption and capacity accounting
include ordinary calls and batch slots through one shared frame traversal.
Resolved slots retain their position until their batch drains, so their positions
continue to consume capacity; partial batches cannot multiply retained metadata.
Exact version-1 through version-3 contexts are validated before normalization;
version 1 adds empty batches and null slot identities. These codecs lack
positive fresh-call admission, so normalization closes and ambiguates every
frame and clears even explicit response candidates, including handles and
pending polls. Identities, retired handles and capacity
state remain. Malformed or unknown contexts still fail closed. Normalization
does not reset cursors or claim an old prefix used the new parser.
Capture fingerprints use this normalized context too; a pre-upgrade capture
with old bracket candidates must be refreshed before current read acceptance.
The refresh preserves immutable snapshots and original numerical observations.
Stored `ResponseBoundary` documents now include closed integer `origin_version=2`.
The exact legacy shape without it decodes as version 1, after validating its
original digest; an explicit version 1 is also historical evidence. Neither
supplies current response proof or historical origin conflicts under the new
policy. Its operation identity still undergoes core success validation. This
prevents refresh before replay from relabelling old proof as current evidence.
`_lifetime_evidence` reads transient lifetime proof independently of success
parsing, at the matched call/slot/poll's inherited transport depth. It admits
exactly one process-result object with integer `exit_code`, optional string
`output`, and optional `chunk_id`, `wall_time_seconds`, `original_token_count`;
no other fields are admitted there. Metadata types and bounds are checked.
The shared `_process_kind` validator recognizes any process metadata before
either lifetime classification or handle extraction. Missing exit codes stay
unresolved unless the complete envelope proves a running top-level session;
malformed metadata cannot admit that session or expose stdout handles.
It also admits one known process-exit header. Every nested header passes the
same lifetime-aware validation; the JSON decoder cannot silently strip another
header. Header metadata fields are unique and their numeric values are bounded;
conflicting status lines cannot yield terminal proof through success parsing
either. Multiple running cell declarations remain ambiguous live aliases.
An outer completed-script header is neutral and still requires its inner result.
String stdout is opaque for
lifetime proof, including non-JSON or incomplete JSON, but retains existing
size/truncation rejection. Structured stdout, unknown wrappers, duplicate
transport keys, conflicting/unknown headers and multiple terminal envelopes cannot
resolve a lifetime through this path. Known outer text/content/output wrappers
retain the existing depth and size bounds. No flattened body `exit_code`
alone is used as proof. Its closed outcome distinguishes process termination,
body-only transport eligible for acknowledgement-based termination, and an
unresolved transport. Invalid process metadata, a nullable or malformed exit
code, a conflicting handle, or unresolved siblings cannot be overridden by
stdout acknowledgements. Mixed acknowledgement objects also validate their
output/content children before allowing body-only termination. Their
independently valid observations are retained.
First-output bracket closure still applies. Handle extraction uses
the known transport containers separately from success-body parsing. It does
not descend into a completed process's stdout, a running or incomplete process's stdout, or
a Taskgov body to discover session handles; separate live result blocks remain
eligible. Lifetime and handle decoding share the source object-pairs policy:
duplicate-key objects are opaque in both object and JSON-string inputs.
Ordinary non-batch parsing leaves damaged containers unresolved while preserving
independent blocks and their established origins. Unused semantic-body children
do not become transport evidence or invalidate their containing acknowledgement.
Strict batch parsing retains its whole-output ambiguity rejection; ordinary
index detection uses the same router without that batch-only rejection.
The shared source JSON decoder replaces each duplicate-key object with an opaque
in-memory marker, discarding its contents before transport interpretation. This
also covers object-valued JSONL output, where later string decoding cannot see
the ambiguity. Batch preflight rejects such markers before any slot mutation;
ordinary parsing retains independently valid siblings. No marker or raw data is
persisted. `source_object` shares this object-pairs rule with ordinary transport
string and literal-poll decoding; batch strict mode still rejects the whole
ambiguous correspondence. `response_payload` validates the source discriminators
and response-item payload before full/incremental projection and direct tracking.
Lost outer identity interrupts pending proof; it is not an irrelevant-record
result. Missing call identity fences pending frames while independently valid
historical operations can still be observed. Interruption closes bracket
candidates without deleting frames, prior evidence or later healthy calls.
Lost call-turn or malformed tool-name structure is checked before returning;
duplicate tracked call IDs are checked before filtering unrelated tool names.
This uses the bounded pending-frame traversal, including live handles and the
overflow frame, plus current call/batch keys. Invalidate every retained frame
with a matched origin, including already-issued poll aliases, without altering
independent origins or prior emitted observations. No new history set is stored.
The common semantic-leaf projection returns valid observations independently
of unreadable evidence, including invalid siblings in one registration array.
The review projector returns this same typed partial result after validating
each receipt entry; an invalid or absent receipt identity cannot erase the
other entries or be hidden by a separate successful acknowledgement.
Missing project identity is invalid before project filtering; only a positively
identified foreign project is irrelevant. For state observations, required
Task/event identities and their agreement precede operation eligibility. The response tracker
consumes this typed result once at the actual turn, then relocates validated
operation metadata to the already-proved originating turn. It never discards
valid observations because the same leaf also contains unreadable evidence;
that flag prevents acknowledgement-based termination without overriding an
independent process exit. Neither the flag nor the source body is persisted.
Review/read/save/wait projectors distinguish missing required metadata from
positively foreign identities. Their shared identity-exclusion predicate validates
each applicable project/sender independently before other required fields;
missing unrelated fields cannot veto an already-proved exclusion. The terminal gate consumes the typed projection's
qualification, never its unvalidated marker labels. Their unsupported or
malformed evidence cannot be erased by another valid success leaf.
A shared closed text-block predicate admits only type/string-text
pairs for routing, semantic parsing, lifetime proof and cell/session extraction.
A terminal poll cannot retire an ambiguous live handle or erase its
unresolved fence, including collisions observed after the poll was issued and
retained across restart. This applies to process and Taskgov terminal results.
No stdout, exit code or additional lifetime flag is persisted.

The response adapter's sanitized prefix is `codex-response-relations-v15` for
positive call admission, indexed batches, persisted first-output closure and
separate process-lifetime evidence, complete header validation and retained
ambiguous lifetimes with a transport-qualified acknowledgement fallback and
shared process-metadata admission across both lifetime paths and batch routing,
retained unproven indices, source-identity loss and representation-independent
ambiguity preservation.
Bounded audit/replay replaces an older prefix's
discarded check results and continuation without changing numerical schema or
deleting original counters/history; normal reads never perform that replay.
Version-8 pending contexts and version-9 through version-14 relation decisions are replaced only
when that registered-source replay
actually rebuilds them. It can recover terminal frames retained by the old
stdout parser and origins lost after those frames exhausted capacity. Merely
loading the new adapter does not purge pending frames or claim those origins
recovered; missing source evidence remains unknown. Immutable snapshots remain
historical evidence and numerical observations are not duplicated by replay.
Live session/cell handles retain the same unresolved origin between polls;
call/handle aliases are deduplicated. A live handle collision invalidates the
retained origin and already-issued poll aliases before future results can bind.
Unknown numerical origins are fenced
using their own source incarnation, record order and explicit turn order,
without retroactively covering earlier turns or preceding responses.

Explicit Setup alone migrates schemas 1–5 atomically, preserving original
observations, cursors, immutable snapshots, membership and cycle links. It
marks old continuations for a new-incarnation replay; ordinary collection never
interprets the old prefix as the new relation format. Missing/corrupt contexts
fail numerical admission rather than silently starting a partial trace. The
schema-5 class and its policy remain available for old-schema validation.

Projection revalidates exact core operations and response identity, then uses
one consistent source-order graph per turn; disconnected/conflicting order is
unknown. Source positions are metadata, not inferred wall-clock Task duration.
The geometry pass remains global. Exact partition identity, metadata basis and
dirty turns select model aggregation; validated unchanged snapshots reuse their
totals. Changed partitions preserve predecessor overlap through original keys.
The capture basis includes closed ingest continuation and source cursor
metadata, so an unresolved call alone can invalidate the previous projection.
Snapshots whose relation was removed by restore or replay are retired through
an overlapping execution's successor without deleting their immutable bytes.
Full and sliced collection must yield the same relations, response sets,
models and diagnostics for one admitted input/core basis. Core restore does
not rebind missing executions, cycles or immutable wait/reviewer anchors.

Snapshot v2 validation is closed and separate from retained v1 validation.
The current `response-partitions-v5` policy requires positive call-origin proof
and applies confirmed pause/block exits
and resume origins. Whole-response owner allocation is separate from applying
the same response's committed state order, so a same-execution stop/resume
leaves the correct final active state. Stopped gaps and previous-turn checks
are not retrofilled. State/preparation resets match the affected execution,
or its Task for a null-ID release; unrelated blocked organization preserves
completion tails and selection series. An actor-changing release uses ordered
core transitions to fence the former actor after its last qualified endpoint,
until a local bound same-Task transition or new start/resume. This does not
compare different threads' clocks or synthesize a Task transition. The original
execution receives the qualified recovery stop response; ambiguous overlapping
work retains partial membership. Null-ID releases resolve their affected
execution from the preceding matching same-Task core transition. Association
admission and placement in the current ordering graph are distinct: an
unplaceable stop fences only that execution's carried ownership until a located
same-Task boundary or a new local origin. An unrelated organization event is
neither recovery nor a reason to invalidate another execution's confirmed
allocation or an unassigned gap. Turn-only fallback is restricted by located
entry/exit anchors and cannot attach another Task to an ordered response
already allocated to a known execution. `owner_segments` preserves each
interval's exact starting and ending core transition IDs in process-local
metadata; the response repository carries those paired origins into fallback.
Repeated same-turn segments of one execution therefore do not borrow another
segment's start or disable its cutoff. The legacy `owner_intervals` projection
retains its existing interval shape and meaning. No segment metadata is stored.
Unordered original keys may retain
partial membership; no missing response position is fabricated.
Pending origins are located from source/incarnation/turn/record-order metadata
before partitioning. The pure classifier records the carried active/completed
execution set at each whole response. After allocation it fences a pending
origin until a new qualified local start/resume, or a located covered-state
operation set that re-establishes all carried/origin-response owners. Unrelated
organization releases are excluded. Empty carried state does not permit an
organization update to certify an unassigned interval. Terminal-result replay
uses the unchanged call identity and original response, never its arrival time.
No SQL structure, core write or result-body retention is introduced.
Retained `response-partitions-v1`, `response-partitions-v2`, `response-partitions-v3`
and `response-partitions-v4` snapshot bodies remain
valid unchanged; algorithm identity is part of the capture and geometry basis,
forcing new successors instead of reusing old-policy totals. A read before
permitted refresh returns the existing pending/unavailable outcome without
writing. Existing numerical collection/publication invokes that refresh and
publishes v5 successors; it preserves prior snapshot bytes, membership and
cycle links and needs no Setup, new hook/LLM action or core write. No DDL changes.
Task totals and work-unit parts union original keys; shared parts name actual
Task/execution identities instead of displaying another period's shared total
as an exclusive value. The v2 index exposes known-unassigned versus unknown
observed values without a completion gate or new hook/LLM action. Current
factories select the schema-7 publication subclass through `usage_collection.py` and
`usage_evidence_service.py`; lifecycle composition and source discovery stay
with their existing owners.

### Review Wait Decision Structure

`usage_wait_attribution.py` validates the closed helper marker and projects its
actual sender's host turn as a single-turn interval. It accepts no caller turn
ID, verdict, timer body or inferred delivery time. Parent echoes fail the
sender-to-source comparison. Duplicate markers for one execution/manifest use
the earliest explicit same-sender host turn; conflicting senders or unavailable
order supply a fixed unknown-boundary diagnostic instead of extra usage.
The existing response union and execution-period graph handle overlaps, late
records and reopen separation.

`usage_wait_repository.py` extends the schema-3 evidence repository with
numerical schema 4 and `usage_wait_turns`. Explicit setup migrates admitted
schemas 1, 2 or 3 atomically, preserves observations, cursors and immutable
snapshots, and records the new migration marker last. Old readers reject the
new schema; ordinary collectors and helpers never initialize or migrate it.
The existing schema-2/3 repository implementations retain their old meanings.

The repository also supplies the helper's core read validation. One admitted
snapshot compares the prepared Packet's project, Task, Contract, full target
and execution with current state and returns the existing artifact-manifest ID.
There is no new core relation or synthetic Receipt. Later projection verifies
that exact manifest and its matching authority/criterion Reference plus the
original execution; it does not require the old target to remain current.
The schema-4 capture/read basis includes the referenced immutable anchor state,
so a restore cannot leave a previously captured total accepted after its anchor
disappears. SQL remains in this repository; host control and Packet transport
remain outside it. Only allowlisted structural metadata reaches storage or
prefix hashes.

The integrated worker's actual receipt adapter calls the existing numerical
repository only after matching a new same-parent structured incoming event.
`record_host_receipt` registers that real parent and stores its typed
`WaitObservation` in the existing schema-4 relation. The adapter first validates
the retained execution/manifest anchor in an admitted core read, then closes it
before the numerical write. It runs only when collection is already enabled;
missing/unmigrated numerical state remains unavailable. No current-owner lookup,
message parser, new schema, implicit setup or invented response is introduced.
Existing interval union and late immutable publication cover the real reporting
turn even when the preceding integrated completion released ownership.
The wait adapter supplies the first matched turn after its send-time boundary,
including one found behind later same-parent turns. Association uses each retained
execution/manifest independently; shared-turn response keys are unioned, and no
latest-owner lookup or separate usage schema is needed.

### Immutable Usage Persistence

`usage_evidence.py` owns the closed metadata-only snapshot format and validation.
`usage_evidence_repository.py` owns numerical schema 3 and its setup-only atomic
schema-1/2 migration. It preserves all original observations and cursors and adds
`usage_snapshots`, `usage_snapshot_members`, `usage_cycle_links`,
`usage_supersessions`, and the mutable singleton `usage_capture`. Update/delete
triggers protect the first four tables. The capture fingerprint contains only
core transition identities/structural fields, exact execution-cycle links and
reviewer bindings; it is a read-basis check, never Task authority or a gate.

One numerical writer precedes a fresh admitted core read. Attribution reuses
that numerical connection, eliminating a second inconsistent numerical read.
It derives completion periods from ordered core transitions and links every
pre-completion execution to the immutable cycle selected by its final execution.
Snapshots, original-key membership, supersession, cycle links and capture basis
commit together. An unchanged current component is reused. A changed component
includes predecessors in its hashed identity, so later counter/conflict changes
cannot revive an already superseded snapshot or form a successor cycle.
No callback touches the core writer; late replay requires no ownership takeover.

`usage_evidence_service.py` supplies safe existing-Task JSON projection and a
replayable internal publication worker. The optional lifecycle adapter invokes
it; it is not a public command or a hook installer. It uses the resolver's usage paths and the existing physical
path, durable-file, no-replace and atomic-replace primitives. A zero-wait usage
lock serializes capture/publication; snapshots are durable before index replace.
Files are separately capped at 16 MiB; overflow/failure retains last-good index
and pending publication, not a truncated or complete claim. Immutable bytes
are matched, never repaired in place. Reads validate closed stored snapshot
shape, digest, members and counters before output; errors become fixed unknown
usage, not private diagnostics or a failed Task read. Only explicit setup
migrates; ordinary completion does not even open the numerical store.

`usage_publication_repository.py` adds numerical schema 7 and the mutable
singleton `usage_publication` (`revision`, exact index `basis`, `position`,
`total`, `published`, canonical-field `checksum`). The checksum detects damaged
continuation fields; it is neither authentication nor evidence of unchanged
external bytes. Setup alone adds it and the migration marker. Schema-6
continuation bytes remain unchanged; migrations from older schemas retain the
existing response-format replay rule. The exact predecessor admitted under the
migration writer selects that rule, never an earlier inspection. No observation or immutable history is
deleted or treated as already audited during migration.

Capture returns its admitted current documents, core/period basis and
classification to the publisher in the same numerical writer/core read.
It builds only index metadata for history, without repeating historical
membership/counter validation or classification. Ordinary response reads reuse
the base reader's validated documents and periods in that same connection;
there is no cross-connection verified cache. `usage_snapshot_read.py` owns an
explicit read value with validated documents, member/predecessor maps, original
responses and execution/response/partition indexes. It belongs to the active
numerical transaction, checks its lifetime and cooperative budget, and is passed
through capture, overlap selection and totals rather than saved on a repository.
Snapshot identities and missing original keys are fetched in bounded batches,
with a budget/lifetime check before each batch. The cache deduplicates across
batches only within that read; original validation and per-document byte limits
are unchanged. A large union of valid documents is not itself one document.
Display validates all current metadata before selecting the Task's components,
so corrupt execution metadata cannot silently hide a selected original. Only
those components' members, counters and predecessor edges are read in full;
shared components and all this Task's periods contribute to its union total.
The publication basis also includes
closed turn, operation, reviewer, conflict and source-diagnostic inputs, plus
actual operation resolutions, so changed source/capture/restore state cannot
silently reuse an earlier index. Structural metadata remains a whole pass.

The writer closes before file I/O. Each subsequent short numerical read fully
validates one snapshot; the service compares/publishes its immutable bytes,
then CAS-advances the saved position. A losing cursor never regresses a winner.
A new event retains the cursor only for the identical prospective index and
an unfinished sweep, otherwise starts at zero. A final fresh numerical/core
read validates the saved cursor, capture, no pending dirty turns and exact
index metadata, and remains open during index replacement to fence concurrent
normal writers. The published flag is saved afterward; an interruption at
that boundary permits idempotent index replay. No core writer spans this work.

The saved position is completed work, not an unchanged-byte certificate. The
index's `integrity` and internal `evidence_verification_deferred` diagnostic
always disclose the rotating audit's detection window. Earlier rows/files may
change between steps and are checked again by the next sweep; stat metadata
never substitutes for content validation. File failures retain last-good index
and cursor through the last committed step. One item and its checkpoint must
fit the event's remaining cooperative budget to advance; no universal timing
guarantee or new daemon, timer, hook event, command or LLM step is introduced.

<a id="schema-v20-physical-foundation"></a>

## Schema-v20 Physical Foundation

One internal storage/repository seam accepts an explicitly injected
caller-owned disposable v19 database. It migrates that database in place at the
same path and is the sole non-public exception to the rule that explicit setup
is the only migrator. It is not routed from the CLI, setup planner,
canonical-state resolver, maintenance, managed backup/recovery, Evidence, or
Viewer. It performs no copy, backup, publication, or path replacement. Public
`SCHEMA_VERSION`, `setup.py`, and all schema-v19 writer/reader routes remain
unchanged.

The migration marker is exactly version `20`, name
`verification_runner_shadow`. A complete v19 source is required. To permit the
Bundle table rebuild, foreign-key enforcement is disabled before
`BEGIN IMMEDIATE` and remains disabled for the entire migration transaction.
`PRAGMA foreign_key_check` must return no row before commit; after commit or
rollback, a `finally` path restores and rechecks foreign-key enforcement as on.
Any error rolls the transaction back. This guarantees logical schema/data
restoration, not byte-identical SQLite file restoration. Lock contention maps
to the existing busy outcome, and no partial column, object, copied row, or
marker may survive. Reentry on exact v20 is validation-only. A missing or
changed v20-owned object, a wrong owned column, marker-only state, a known later
migration marker, a conflicting object using an owned name, integrity failure,
or foreign-key failure is rejected rather than repaired. An unrelated extra
object outside the owned names and not attached to the owned Bundle table being
rebuilt is not, by itself, a schema-v20 failure.

A persistent index or trigger with an unowned name but with
`sqlite_master.tbl_name = 'completion_evidence_bundles'` is unsupported
attached residue. Successful migration deletes it with the old v19 Bundle
table and never replays arbitrary attached DDL. Transaction rollback restores
it, and reentry observes its established absence. No other unrelated object is
deleted by this rule.

### Exact Schema-v20 Physical Contract

The final required inventory is 35 tables including `schema_migrations`, 42
explicit indexes, and 59 triggers. Relative to v19, migration 20 adds four
tables, ten explicit indexes, and twelve triggers; appends five columns;
rebuilds only `completion_evidence_bundles`; and replaces one existing
criterion-link matrix trigger without changing the trigger count. Public
schema-v20 activation adds no further DDL.

This contract owns physical structure, not the future Runner decision model.
The `plan_state`, `coverage`, `route`, `launch_state`, `outcome`, and `reason`
values are bounded storage codes only. The `trigger` and `event_kind` constants
identify structural record kinds without granting business meaning. Closed
taxonomies and cross-field matrices, pending-cleanup admission, and cleanup
acceptance belong to the target-plan, process, and parent-service boundaries.

The following predicates are literal DDL abbreviations, not open design slots:

- `runner_id(value,prefix)`: `value` is `TEXT`, its length equals the prefix
  length plus 16, it starts with `prefix`, and its 16-character suffix contains
  no character outside `[0-9a-f]`.
- `hex64(value)`: `value` has length 64 and contains no character outside
  `[0-9a-f]`.
- `sha256(value)`: `value` has length 71, starts with `sha256:`, and
  `hex64(substr(value,8,64))` holds.
- `code(value)`: `value` has length 1 through 64, its first character is in
  `[a-z]`, and every later character is in `[a-z0-9_]`; equivalently
  `length(value) BETWEEN 1 AND 64 AND substr(value,1,1) GLOB '[a-z]' AND
  substr(value,2) NOT GLOB '*[^a-z0-9_]*'`.
- `text_n(value,n)`: `value` has length 1 through `n`. A nullable use is exactly
  `value IS NULL OR` the named predicate.

The four immutable tables have no column defaults and use this exact column
order and independent column checks:

- `verification_runner_resolutions`: `verification_runner_resolution_id TEXT
  PRIMARY KEY` with `runner_id` prefix `tg_verification_runner_resolution_`;
  `project_id TEXT NOT NULL`; `task_id TEXT NOT NULL`; `contract_revision
  INTEGER NOT NULL CHECK (contract_revision >= 1)`; `authority_snapshot_id TEXT
  NOT NULL`; `verification_criterion_id TEXT NOT NULL`;
  `verification_expectation_digest TEXT NOT NULL` with `hex64`;
  `verification_criterion_digest TEXT NOT NULL` with `sha256`; `target_kind
  TEXT NOT NULL` with `code`; `target_value TEXT NOT NULL` with
  `text_n(target_value,500)`; nullable `target_base_revision TEXT` with
  `text_n(target_base_revision,128)`; `target_generation INTEGER NOT NULL CHECK
  (target_generation >= 1)`; `target_capture_version INTEGER NOT NULL CHECK
  (target_capture_version = 1)`; `artifact_manifest_id TEXT NOT NULL`; nullable
  `target_material_digest TEXT` with `sha256`; `plan_state TEXT NOT NULL` with
  `code`; nullable `plan_blob_object_id TEXT` with
  `text_n(plan_blob_object_id,500)`; nullable `plan_raw_digest TEXT` with
  `sha256`; nullable `plan_id TEXT` with `text_n(plan_id,200)`; nullable
  `plan_version INTEGER CHECK (plan_version >= 1)`; nullable
  `plan_semantic_digest TEXT` with `sha256`; nullable `selected_entry_digest
  TEXT` with `sha256`; `coverage TEXT NOT NULL` with `code`; `step_count INTEGER
  NOT NULL CHECK (step_count BETWEEN 0 AND 16)`; `runner_contract_version
  INTEGER NOT NULL CHECK (runner_contract_version = 1)`;
  `runner_implementation_version TEXT NOT NULL CHECK
  (runner_implementation_version = 'taskgov-verification-runner/1')`;
  `runner_implementation_digest TEXT NOT NULL` with `sha256`;
  `runner_policy_digest TEXT NOT NULL` with `sha256`; nullable `runtime_digest
  TEXT` with `sha256`; `gate_eligibility_version INTEGER NOT NULL CHECK
  (gate_eligibility_version = 0)`; `trigger TEXT NOT NULL CHECK
  (trigger = 'review_target_set_v1')`; `route TEXT NOT NULL` with `code`;
  nullable `reason TEXT` with `code`;
  `idempotency_digest TEXT NOT NULL` with `sha256`; `created_at TEXT NOT NULL`.
- `verification_runner_attempts`: `verification_runner_attempt_id TEXT PRIMARY
  KEY` with `runner_id` prefix `tg_verification_runner_attempt_`; `project_id
  TEXT NOT NULL`; `task_id TEXT NOT NULL`; `target_generation INTEGER NOT NULL
  CHECK (target_generation >= 1)`; `gate_eligibility_version INTEGER NOT NULL
  CHECK (gate_eligibility_version = 0)`; `verification_runner_resolution_id TEXT
  NOT NULL`; `target_material_digest TEXT NOT NULL` with `sha256`;
  `runner_implementation_digest TEXT NOT NULL` with `sha256`; `attempt_digest
  TEXT NOT NULL` with `sha256`; `intent_recorded_at TEXT NOT NULL`.
- `verification_runner_sandbox_events`: `verification_runner_sandbox_event_id
  TEXT PRIMARY KEY` with `runner_id` prefix
  `tg_verification_runner_sandbox_event_`; `project_id TEXT NOT NULL`; `task_id
  TEXT NOT NULL`; `target_generation INTEGER NOT NULL CHECK (target_generation
  >= 1)`; `verification_runner_attempt_id TEXT NOT NULL`; `event_kind TEXT NOT
  NULL CHECK (event_kind = 'attempt_cleanup_succeeded')`; `event_digest TEXT NOT
  NULL` with `sha256`; nullable `terminal_observation_id TEXT`; `created_at TEXT
  NOT NULL`. The fixed code names the record shape only: no table
  check requires a terminal observation or treats the row as accepted cleanup.
- `verification_runner_observations`: `verification_runner_observation_id TEXT
  PRIMARY KEY` with `runner_id` prefix `tg_verification_runner_observation_`;
  `project_id TEXT NOT NULL`; `task_id TEXT NOT NULL`; `target_generation
  INTEGER NOT NULL CHECK (target_generation >= 1)`; `gate_eligibility_version
  INTEGER NOT NULL CHECK (gate_eligibility_version = 0)`;
  `verification_runner_resolution_id TEXT NOT NULL`; nullable
  `verification_runner_attempt_id TEXT`; `runner_implementation_digest TEXT NOT
  NULL` with `sha256`; `route TEXT NOT NULL` with `code`; `launch_state TEXT NOT
  NULL` with `code`; `outcome TEXT NOT NULL` with `code`; nullable `reason TEXT`
  with `code`; `complete_plan INTEGER NOT NULL CHECK (complete_plan IN (0,1))`;
  `total_step_count INTEGER NOT NULL CHECK (total_step_count BETWEEN 0 AND 16)`;
  `completed_step_count INTEGER NOT NULL CHECK (completed_step_count BETWEEN 0
  AND total_step_count)`; nullable `failed_step_ordinal INTEGER CHECK
  (failed_step_ordinal BETWEEN 1 AND total_step_count)`; `started_at TEXT NOT
  NULL`; `finished_at TEXT NOT NULL`; `duration_ms INTEGER NOT NULL CHECK
  (duration_ms >= 0)`; nullable `cpu_time_ms INTEGER CHECK (cpu_time_ms >= 0)`;
  nullable `peak_job_memory_bytes INTEGER CHECK (peak_job_memory_bytes >= 0)`;
  nullable `total_process_count INTEGER CHECK (total_process_count >= 0)`;
  `sanitized_result_digest TEXT NOT NULL` with `sha256`; `created_at TEXT NOT
  NULL`. No physical check pairs route, launch, outcome, reason, plan completion,
  resource fields, attempt presence, or cleanup.

Every new migration-20 Runner foreign key uses `ON UPDATE RESTRICT ON DELETE
RESTRICT`. All new Runner foreign keys are `NOT DEFERRABLE` except the
event-to-terminal-observation cycle, which is `DEFERRABLE INITIALLY DEFERRED`.
The exact new column mappings are:

- resolution `(project_id,task_id)` -> Task `(project_id,task_id)`;
  `(project_id,task_id,authority_snapshot_id)` -> authority snapshot
  `(project_id,task_id,authority_snapshot_id)`;
  `(project_id,task_id,verification_criterion_id)` -> Contract criterion
  `(project_id,task_id,criterion_id)`; and
  `(project_id,task_id,artifact_manifest_id)` -> artifact manifest
  `(project_id,task_id,artifact_manifest_id)`;
- attempt `(project_id,task_id,target_generation,
  verification_runner_resolution_id)` -> the same four-column resolution
  parent key;
- observation `(project_id,task_id,target_generation,
  verification_runner_resolution_id)` -> the resolution parent key, and its
  nullable `(project_id,task_id,target_generation,
  verification_runner_attempt_id)` -> the attempt parent key;
- sandbox event `(project_id,task_id,target_generation,
  verification_runner_attempt_id)` -> the attempt parent key, and its nullable
  `(project_id,task_id,target_generation,terminal_observation_id)` -> the
  observation parent key;
- Bundle `(project_id,task_id,target_generation,
  verification_runner_observation_id)` -> the observation parent key.

The Bundle rebuild restores every pre-v20 foreign-key clause verbatim from the
accepted v19 definition. It therefore preserves each clause's implicit
`NO ACTION` behavior without adding explicit `ON UPDATE` or `ON DELETE` text,
and the existing completion-cycle foreign key remains `DEFERRABLE INITIALLY
DEFERRED`. The `RESTRICT` rule above applies only to new migration-20 Runner
foreign keys and does not rewrite an existing Bundle clause.

Migration 20 creates exactly these ten indexes. `UNIQUE` is stated explicitly;
every other index is non-unique:

| Index | Ordered columns | Constraint |
|---|---|---|
| `idx_verification_runner_resolutions_parent` | `project_id, task_id, target_generation, verification_runner_resolution_id` | `UNIQUE` parent key |
| `idx_verification_runner_resolutions_task_generation` | `project_id, task_id, target_generation` | lookup |
| `idx_verification_runner_attempts_parent` | `project_id, task_id, target_generation, verification_runner_attempt_id` | `UNIQUE` parent key |
| `idx_verification_runner_attempts_task_generation` | `project_id, task_id, target_generation` | lookup |
| `idx_verification_runner_attempts_resolution` | `project_id, task_id, target_generation, verification_runner_resolution_id` | lookup |
| `idx_verification_runner_sandbox_events_attempt_kind` | `project_id, task_id, target_generation, verification_runner_attempt_id, event_kind` | lookup |
| `idx_verification_runner_observations_parent` | `project_id, task_id, target_generation, verification_runner_observation_id` | `UNIQUE` parent key |
| `idx_verification_runner_observations_task_generation` | `project_id, task_id, target_generation` | lookup |
| `idx_verification_runner_observations_resolution` | `project_id, task_id, target_generation, verification_runner_resolution_id` | lookup |
| `idx_verification_runner_observations_attempt` | `project_id, task_id, target_generation, verification_runner_attempt_id` | lookup with exact predicate `WHERE verification_runner_attempt_id IS NOT NULL` |

These indexes impose no per-generation or per-parent attempt, event, or
observation cardinality. Such admission rules belong to the Runner service.

Migration 20 creates exactly twelve Runner triggers. The eight immutable
triggers have these exact timing/table/body definitions:

| Trigger | Exact definition |
|---|---|
| `trg_verification_runner_resolutions_no_update` | `BEFORE UPDATE ON verification_runner_resolutions FOR EACH ROW BEGIN SELECT RAISE(ABORT,'runner_storage_immutable'); END` |
| `trg_verification_runner_resolutions_no_delete` | `BEFORE DELETE ON verification_runner_resolutions FOR EACH ROW BEGIN SELECT RAISE(ABORT,'runner_storage_immutable'); END` |
| `trg_verification_runner_attempts_no_update` | `BEFORE UPDATE ON verification_runner_attempts FOR EACH ROW BEGIN SELECT RAISE(ABORT,'runner_storage_immutable'); END` |
| `trg_verification_runner_attempts_no_delete` | `BEFORE DELETE ON verification_runner_attempts FOR EACH ROW BEGIN SELECT RAISE(ABORT,'runner_storage_immutable'); END` |
| `trg_verification_runner_sandbox_events_no_update` | `BEFORE UPDATE ON verification_runner_sandbox_events FOR EACH ROW BEGIN SELECT RAISE(ABORT,'runner_storage_immutable'); END` |
| `trg_verification_runner_sandbox_events_no_delete` | `BEFORE DELETE ON verification_runner_sandbox_events FOR EACH ROW BEGIN SELECT RAISE(ABORT,'runner_storage_immutable'); END` |
| `trg_verification_runner_observations_no_update` | `BEFORE UPDATE ON verification_runner_observations FOR EACH ROW BEGIN SELECT RAISE(ABORT,'runner_storage_immutable'); END` |
| `trg_verification_runner_observations_no_delete` | `BEFORE DELETE ON verification_runner_observations FOR EACH ROW BEGIN SELECT RAISE(ABORT,'runner_storage_immutable'); END` |

Each remaining trigger is `BEFORE INSERT FOR EACH ROW`; its `WHEN NOT EXISTS`
subquery is the corresponding exact parent predicate below, and its body is
`BEGIN SELECT RAISE(ABORT,'runner_parent_inconsistent'); END`. There is no
alternate action. The four exact parent predicates are:

- `trg_verification_runner_resolutions_parent_insert` joins required parents
  `tasks` as `t`, `authority_snapshots` as `s`, `artifact_manifests` as `m`,
  verification `contract_criteria` as `vc`, and its required verification
  `authority_snapshot_criteria` membership. It requires matching
  `project_id/task_id`; `t.current_contract_revision = NEW.contract_revision`;
  `t.review_target_authority_snapshot_id = NEW.authority_snapshot_id`;
  `t.review_target_acceptance_criterion_id IS m.acceptance_criterion_id`;
  `t.review_target_verification_criterion_id = NEW.verification_criterion_id`;
  `t.review_target_kind = NEW.target_kind`; `t.review_target_value =
  NEW.target_value`; `t.review_target_base_revision =
  COALESCE(NEW.target_base_revision,'')`; `t.review_target_generation =
  NEW.target_generation`; `t.review_target_capture_version =
  NEW.target_capture_version`;
  `t.review_target_artifact_manifest_id = NEW.artifact_manifest_id`; and
  `t.review_target_runner_basis_version = 0`. Snapshot `s` must have the same
  owner and ID, `s.contract_revision = NEW.contract_revision`, and
  `s.verification_digest = NEW.verification_expectation_digest`. Criterion `vc`
  must have ID `NEW.verification_criterion_id`, kind `verification`, and
  `vc.digest = NEW.verification_criterion_digest`, with a snapshot membership
  for `NEW.authority_snapshot_id`. Manifest `m` must have ID
  `NEW.artifact_manifest_id`; the same `project_id`, `task_id`,
  `authority_snapshot_id`; `m.acceptance_criterion_id IS
  t.review_target_acceptance_criterion_id`; `m.verification_criterion_id =
  NEW.verification_criterion_id`; `m.target_kind = NEW.target_kind`;
  `m.target_value = NEW.target_value`; `m.target_base_revision =
  COALESCE(NEW.target_base_revision,'')`; `m.target_generation =
  NEW.target_generation`. Contract revision is bound by Task plus snapshot, and
  capture version is bound by Task; `artifact_manifests` has neither column and
  the trigger performs neither comparison. If
  `t.review_target_acceptance_criterion_id IS NULL`, no acceptance criterion or
  acceptance membership is joined or required. Otherwise the same parent
  predicate additionally requires one same-owner `contract_criteria` row `ac`
  whose ID is `t.review_target_acceptance_criterion_id` and whose kind is
  `acceptance`, plus one same-owner snapshot membership for `ac.criterion_id`.
- `trg_verification_runner_attempts_parent_insert` searches one resolution with
  the same owner, target generation, and resolution ID and requires
  `resolution.target_material_digest = NEW.target_material_digest` and
  `resolution.runner_implementation_digest =
  NEW.runner_implementation_digest`.
- `trg_verification_runner_observations_parent_insert` searches one resolution
  with the same owner, target generation, and resolution ID and requires its
  Runner-implementation digest to equal NEW. If
  `NEW.verification_runner_attempt_id` is non-null, the same `EXISTS` predicate
  also requires one attempt with that owner/generation/ID, the same resolution
  ID, and the same Runner-implementation digest.
- `trg_verification_runner_sandbox_events_parent_insert` searches one attempt
  with the same owner, target generation, and attempt ID. If
  `NEW.terminal_observation_id` is non-null, the same `EXISTS` predicate also
  requires one observation with that owner/generation/ID whose attempt ID
  equals NEW. The fixed event code is not interpreted as cleanup proof.

No trigger scans for an earlier uncleaned attempt or enforces plan, route,
launch, outcome, reason, resource, or cleanup acceptance.

Migration 20 appends `tasks.review_target_runner_basis_version INTEGER NOT
NULL DEFAULT 0 CHECK (review_target_runner_basis_version IN (0,2))`. It then
appends, in this order, nullable `TEXT` columns
`task_completion_cycles.verification_basis_kind` and
`task_completion_cycles.verification_runner_observation_id`; neither cycle
column has a physical `CHECK` or foreign key in schema v20.

`completion_evidence_bundles` is rebuilt from the schema-v19 definition. All
v19 columns keep their exact order and definitions except that
`source_schema_version` and `bundle_version` become `INTEGER NOT NULL` with no
individual single-value `CHECK`; their allowed pair is owned only by the tagged
union below. Nullable `verification_basis_kind TEXT CHECK
(verification_basis_kind IS NULL OR verification_basis_kind IN
('caller_attestation','not_required'))` and nullable
`verification_runner_observation_id TEXT` are inserted, in that order, before
the exact trailing columns `omission_mask`, `sealed_at`, `bundle_digest`, and
`payload_size_bytes`. The rebuild adds the Bundle observation foreign key
listed above and this exact tagged-union `CHECK`:

```sql
(
  source_schema_version = 19
  AND bundle_version = 1
  AND verification_basis_kind IS NULL
  AND verification_runner_observation_id IS NULL
)
OR (
  source_schema_version = 20
  AND bundle_version = 2
  AND verification_basis_kind = 'caller_attestation'
  AND verification_receipt_id IS NOT NULL
  AND verification_runner_observation_id IS NULL
)
OR (
  source_schema_version = 20
  AND bundle_version = 2
  AND verification_basis_kind = 'not_required'
  AND verification_receipt_id IS NULL
  AND verification_runner_observation_id IS NULL
)
```

The rebuild restores `idx_completion_evidence_bundles_task_cycle`, the Bundle
immutable trigger pair, and the existing member, Finding-snapshot, and
cycle-basis matrix triggers from the accepted v19 definitions. It replaces
`trg_criterion_evidence_links_matrix_insert` with the v19 arms unchanged plus
one dormant physical arm for relation `runner_observation`: its Evidence
Reference must use source kind `runner_observation`, its criterion must equal
the Reference verification criterion, and attribution must be exactly
`machine_observed`, producer `verification_runner`, version `1`. Migration
creates no Runner Reference, criterion link, projection, or Bundle member; the
parent service owns the first durable audit mapping and write.

Only those owned Bundle objects are restored. Any persistent unowned index or
trigger attached to the old Bundle table is deleted with that table on a
successful migration; its arbitrary DDL is not replayed. A rollback restores
the attached object transactionally, while unrelated standalone objects remain
unchanged.

For every v19 row the new Task marker is `0`, both new cycle columns are null,
and both new Bundle columns are null. All four Runner tables and all new Runner
Evidence/criterion-link sets are empty. Every pre-v20 table is compared on its
original ordered columns, so all business rows/IDs and existing version-1
Bundle payload bytes/digests remain equal. The version-20 marker is inserted
only after those checks; exact schema validation, full storage validation,
`quick_check`, and foreign-key check precede commit.

Focused migration tests cover normalized `sqlite_master`, `table_xinfo`,
foreign-key, ordered-index, and trigger inventory; permissive physical
cardinality; the null-Runner Bundle union; a positive resolution parent with
nullable acceptance and present verification criterion; marker-only and
partial-owned-object drift; unrelated standalone objects; successful retirement
and rollback restoration of unowned Bundle-attached indexes/triggers; v19 row,
ID, Bundle-byte, and object preservation; reentry; rollback; and contention.
Public setup/schema behavior is tested separately from the storage-only
migration helper so test imports do not expand that helper's authority.

The final storage model deliberately omits the legacy shim fields
`sandbox_provider`, `sandbox_policy_digest`, and `sandbox_instance_digest`.
When constructing or verifying the compatible digest/source projection, the
storage adapter supplies literal null for `sandbox_provider` and
`sandbox_policy_digest` on a resolution and for `sandbox_instance_digest` on an
attempt. These adapter-only keys are never persisted as schema-v20 columns.
No provider/policy/instance shim is an active storage or Evidence consumer.

### Schema-v20 Public Contract

The public schema-v20 contract adds no further table, column, index, or trigger.
It owns the schema constant/setup target, Bundle-v2 null-Runner writer,
Evidence JSON, Viewer, and managed backup/recovery compatibility. Schema
activation itself creates no Runner resolution, attempt, sandbox event,
observation, Evidence Reference/link, Bundle member, or Runner projection; the
parent service owns those audit writes. Canonical migration occurs only through
explicit public setup, never as an implicit effect of Git materialization.

The public storage admission layer classifies complete v19, complete v20, and
hybrid state before invoking migration or another schema-aware consumer. A
database whose migration history declares v19 but that contains any recognized
v20-owned table, explicit index, trigger, or column is rejected before any
database or sidecar write, migration backup, recovery copy/publication, Viewer
publication, or managed-backup write. The canonical resolver, setup inspection/migration,
Viewer source validator, and managed backup/recovery validator all use this
same owned-inventory check. Complete v19 reaches migrations 20, 21, and 22;
complete v20 reaches 21 and 22; complete v21 reaches 22; exact v22 takes
validation-only reentry. Unrelated extra objects retain the migration-20 policy: an
unowned index/trigger attached to the Bundle table is removed
only by a successful complete-v19 migration, while unrelated standalone
objects remain unchanged. Migration-21 hybrid markers are recognized before
any write.
Owned-marker recognition uses SQLite `NOCASE` identifier equality across
catalog object types and scopes ordinary or generated columns to their
designated parent table.
Complete-v20 admission uses one shared full audit-graph validator. It admits
the empty state, a single pending
intent, a cleanup-only restart terminal, or the exact complete terminal graph
defined by the [Runner specification](runner-execution-specification.md#parent-service-and-audit-graph);
it rejects every malformed, duplicate,
foreign-owned, partially linked, or gate-eligible variant. Task Runner basis
remains zero and cycle/Bundle Runner observation pointers remain null, while
native Bundle-v2 `caller_attestation` or `not_required` basis is admitted. The
validator is repeated in each independent operational read, writer, Viewer,
backup, and recovery snapshot rather than cached across them.

At schema v20 the native completion transaction derived
`verification_basis_kind` as `caller_attestation` when a qualifying
Verification Receipt is linked and as `not_required` for trimmed-empty
verification, wrote Bundle version 2/source schema 20, and always wrote a null
Runner-observation pointer. Schema v21 retains those manual branches and
additionally writes the
exact qualifying `runner_observation` branch, reusing its existing Runner
Reference and criterion link as Bundle members. Existing Bundle-v1 payload bytes
and digests remain immutable, and Evidence JSON may project both preserved v1
and native v2 Bundles. The manual branches create no Runner member or projection;
the Runner branch uses only its already-sanitized stored observation.

`evidence_projection.py` selects the format from the validated source schema,
never from caller input. For schemas v20-v22 it extends the v1 payload key set only
with `verification_basis` and `runner_observation`. The former is the exact
four-key object fixed by the [shared Runner protocol](specification.md#schema-v21-persistence-compatibility-and-shared-runner-protocol);
the latter is null for manual
branches and populated only for the qualifying Runner branch. It
seals the sorted-key compact payload with
`taskgov-completion-evidence-bundle-v2\0`, writes envelope
`format_version=2`, and recomputes the stored digest and byte count from those
exact bytes. Projection reconstructs and validates a stored v1 or v2 Bundle by
its own tagged-union row without converting either format.

The schema-v20-through-v22 index uses `taskgov-evidence-index-v2\0`, envelope
`format_version=2`, and the prior entry key set plus
`bundle_format_version`. The new field is null for legacy entries, 1 for a
preserved v1 Bundle, and 2 for a native v2 Bundle. The index remains ordered
and index-last; its other limits and publication behavior are unchanged.

<a id="schema22-reservation-cleanup-design"></a>

<a id="current-schema-v22-reservation-cleanup-design"></a>

## Supported Schema-v23 Verification Declaration Design

`schema_verification_declaration.py` owns migration 23 and exact owned-DDL
validation. `storage.py::apply_migrations` composes it after complete schema 22.
Two ALTERs add the bounded reason columns: Task text defaults to empty; cycle
text remains nullable for old history. A native insert requires a non-NULL exact
Task reason with the correct required/waived basis. The pre-existing partial
legacy bridge alone admits a new NULL. Global stored-row validation checks the
reason's storage type, bounds, privacy and exclusive declaration; native source
23 Bundle/cycle validation rejects NULL, while old Bundles keep NULL history.

The Bundle table is rebuilt with the unchanged format-2 three-arm union and a
new source-23 case; the cycle Evidence guard now requires source 23. The sole
temporary name is `completion_evidence_bundles_v22`. Original-column business
snapshots and unrelated-object SQL must match before and after the marker; new
Task reasons must remain empty and old cycle reasons NULL. Existing FK-off /
legacy-alter-on transaction, marker-last, rollback, connection restoration,
pre-migration backup and validation-only reentry are retained. The owned
inventory is 35 tables, 42 indexes and 60 triggers. No general migration or
admission abstraction is added.

A reason is not part of the authority-snapshot or Bundle digest. Task editing
invalidates the target explicitly even when the authority snapshot is reused;
the Packet exposes the reason and each new cycle stores its immutable copy.
Done/reopen validation compares a non-NULL cycle reason with its Task; a legacy
NULL is never inferred to mean approval. Recovery and doctor retain global
admission; ordinary Task resolver/writer scope follows operation-local admission.
Viewer ignores the new reason fields and keeps
its existing v4 shape. Schema-23 capture/index writes source 23, but immutable
source-19 through source-22 Bundles keep their exact bytes and digests.

### Supported Schema-v22 Reservation Cleanup

The predecessor schema-22 boundary composes the existing
migration sequence through `_migrate_schema22_connection`. A complete source
20 runs 21 then 22, source 21 runs 22, and fresh/older construction reaches the
same final v22 objects and contiguous markers 1 through 22. Exact-v22 reentry
opens a validation-only transaction and rolls it back without a write.

Migration `22/evidence_reservation_cleanup` rebuilds exactly
`evidence_references`, `criterion_evidence_links`, and
`completion_evidence_bundles`. Their unchanged ordered business columns retain
all valid rows and IDs. The 14 owned replacement objects comprise those tables,
their indexes/triggers, and `trg_task_completion_cycles_evidence_basis_insert`;
the full inventory remains 35 tables, 42 explicit indexes, and 59 triggers.
Current DDL and enum/order dispatch remove only `derived_analysis` source and
relation, `llm_derived` assurance, and `batch_analyzer` producer. Old version-
specific definitions remain source-admission/compatibility owners.

The helper requires no active transaction, `foreign_keys=ON`, and
`legacy_alter_table=OFF`. It sets foreign keys off and legacy alter on before
`BEGIN IMMEDIATE`, validates complete source 21, and rejects unowned attachments
to those three tables and exact temporary-name collisions before DDL. Existing
stored-source validation rejects retired-value rows without conversion or
deletion. The only temporary names are `evidence_references_v21`,
`criterion_evidence_links_v21`, and `completion_evidence_bundles_v21`.

It snapshots all business tables on their original ordered columns and the
SQL of objects outside the replacements; drops the coupled Evidence cycle
guard; renames/recreates/copies the three tables; drops their old copies in
reverse order; and restores the owned objects and cycle guard. It proves row
and unrelated-object equality, exact owned structure, quick check, and foreign
keys before inserting marker 22 as the last mutation. Full v22 validation and
another preserved-row comparison follow before commit, so an admitted unrelated
marker trigger cannot silently change business rows. Failure rolls back the
whole logical transaction; `finally` restores and verifies both connection
settings. Reentry validates attachments/temporary-name absence and never repairs.

The Bundle CHECK retains source-19/v1 and source-20/21/v2 arms unchanged and adds
source-22/v2 with the same three basis arms as source 21. The locked completion
basis carries the validated physical source version into both payload and row;
the native cycle guard requires source 22. Shared stored-row validation compares
Bundle source with the observed container, including selected-history reads,
so source 22 cannot be accepted in physical 21. Pure encoding remains
source-version-aware and introduces no format or digest domain. Old sealed
Bundle bytes/digests are never relabelled or resealed; index format 2 reports
container 22 while referencing those unchanged files.

Task/Receipt/lifecycle and Runner capability checks admit 22 under the unchanged
schema-v21 protocol below. Current validation, setup/doctor, backup/recovery,
resolver/relocation, and Viewer use their existing source-aware dispatch; no
parallel reader or candidate runtime exists. Global stored-state validation
remains global. Recovery defers only the existing Task-verification privacy/
capacity rejection until structural Runner/Bundle checks complete. Viewer
extends its same-transaction one-shot Task-batch proof to 22, remains snapshot
v4, and discards Evidence/Runner details before its unchanged projection.
Setup retains normal pre-migration backup and matched rollback; no new policy,
config, process/Runner gate, Skill procedure, or public command is introduced.

<a id="schema21-runner-gate-basis-design"></a>

<a id="schema-v21-runner-gate-basis-design"></a>

## Schema-v21 Runner Gate-Basis Design

This section retains the exact schema-v21 migration/storage implementation and
qualifying Runner protocol inherited by schema v22. Its source-21 Bundle and
migration statements describe that supported predecessor; the
[current v22 delta](#schema22-reservation-cleanup-design) owns current source
identity, reservations, setup target, and consumer upper bounds. The manual
Verification Receipt remains the explicit fallback.

### Migration Identity And Exact Owned Delta

Migration 21 is exactly version `21`, name
`verification_runner_gate_basis`. It adds no table, column, explicit index, or
trigger. The final required inventory therefore remains 35 tables including
`schema_migrations`, 42 explicit indexes, and 59 triggers. It rebuilds exactly
these four tables and no other table:

1. `completion_evidence_bundles`;
2. `verification_runner_resolutions`;
3. `verification_runner_attempts`; and
4. `verification_runner_observations`.

Every column retains its schema-v20 name, order, nullability, default, foreign
key, and non-gate check. The only Runner-table DDL change is that
`gate_eligibility_version` on resolutions, attempts, and observations changes
from `CHECK (gate_eligibility_version = 0)` to
`CHECK (gate_eligibility_version IN (0, 1))`. The sandbox-event table has no
gate-eligibility column and is not rebuilt. The Task column remains exactly
`review_target_runner_basis_version INTEGER NOT NULL DEFAULT 0 CHECK
(review_target_runner_basis_version IN (0, 2))`; neither completion-cycle
column is rebuilt or gains a column check or foreign key.

The rebuilt Bundle table retains Bundle version 2 and all schema-v20 columns,
keys, and foreign keys. Its nullable `verification_basis_kind` column check is
widened only to
`caller_attestation|not_required|runner_observation`. Its table-level tagged
union is exactly:

```text
source 19 / Bundle 1 / kind null / Runner pointer null
source 20 / Bundle 2 / caller_attestation / Receipt nonnull / Runner pointer null
source 20 / Bundle 2 / not_required       / Receipt null    / Runner pointer null
source 21 / Bundle 2 / caller_attestation / Receipt nonnull / Runner pointer null
source 21 / Bundle 2 / not_required       / Receipt null    / Runner pointer null
source 21 / Bundle 2 / runner_observation / Receipt null    / Runner pointer nonnull
```

No other source-schema/Bundle-version/basis/pointer combination is valid.
Preserved source-19 and source-20 rows are not rewritten to source 21. A native
schema-v21 completion writes source schema 21 and Bundle version 2; no Bundle
version 3, Evidence Index version 3, new envelope member, or compatibility
conversion is introduced.

Migration 21 recreates under their existing names the one Bundle index, the
nine indexes attached to the three rebuilt Runner tables, and their existing
immutable and parent triggers. Their order, uniqueness, predicates, error
codes, and names remain unchanged except for the eligibility relations below.
It also replaces, without changing the trigger count, exactly these two
cycle-insert guards:

- `trg_task_completion_cycles_verification_basis_insert`; and
- `trg_task_completion_cycles_evidence_basis_insert`.

All other owned tables, columns, indexes, triggers, and their normalized SQL
remain the schema-v20 definitions. In particular,
`trg_verification_runner_sandbox_events_parent_insert` and
`trg_criterion_evidence_links_matrix_insert` already supply the required event
and `runner_observation` Reference/link relations and are unchanged.

### Closed Gate Tags And Parent Guards

`gate_eligibility_version=0` continues to mean audit-only. Version `1` means
only that the exact target generation was selected under the schema-v21 Runner
basis protocol; it does not itself mean pass. Within one Runner graph,
resolution, attempt, and observation eligibility values must be identical.
The current target's Task marker is `0` for an audit-only or ordinary manual-Receipt
target and `2` for a target selected under the version-1 Runner protocol.
Historical graphs retain their stored tags after a later target generation;
only the graph at the Task's exact current target can be a completion basis.

The repository validates native policy identities and measurement combinations
using the [Runner accounting contract](runner-execution-specification.md#runner-policy-and-accounting).
Eligibility-zero graphs retain only the Windows policy; eligibility-one graphs
admit the two closed native identities. The existing policy field and nullable
measurement columns carry this distinction without a schema, migration, DDL,
or gate-eligibility discriminator change. Shared graph validation supplies the same admission
to selected reads, Viewer, backup, and recovery.

The recreated parent guards make these literal changes and no others:

- `trg_verification_runner_resolutions_parent_insert` keeps its complete
  schema-v20 Task/Contract/authority/criterion/manifest/target predicate, but
  replaces `Task marker = 0` with the exact pair
  `(NEW eligibility = 0 AND Task marker = 0) OR
  (NEW eligibility = 1 AND Task marker = 2)`.
- `trg_verification_runner_attempts_parent_insert` additionally requires the
  attempt eligibility to equal its resolution eligibility.
- `trg_verification_runner_observations_parent_insert` additionally requires
  the observation eligibility to equal its resolution eligibility and, when an
  attempt is present, that attempt eligibility to equal it too.
- The sandbox-event guard remains unchanged: its exact attempt and optional
  terminal-observation joins inherit eligibility equality from those parent
  rows.

The full stored-state validator retains the existing four cardinality shapes
(`no admitted attempt`, `pending intent`, `restart cleanup only`, and `complete
terminal graph`) for audit-only graphs and admits the same applicable pending,
cleanup-only, and terminal shapes for a marker-2 eligibility-one current target.
It validates and exposes a structurally sound pending, cleanup-only, or launched
non-pass state to read, Viewer, backup, and recovery consumers; it does not
misclassify such a state as corrupt merely because it cannot complete. A
marker-2 target without its atomic resolution/attempt T1, a current marker/tag
mismatch, or a malformed graph remains invalid. Separately, the completion
basis selector, rather than a caller or a DDL tag alone, recognizes these three
closed cases:

- marker `0`: no gate-eligible current graph; manual
  `caller_attestation|not_required` completion remains available;
- marker `2` plus one complete version-1 terminal graph whose observation has
  `route=m21_fallback`, `launch_state=no_launch`,
  `outcome=blocked_prelaunch`, reason `runtime_unavailable` or
  `process_setup_failed`, `complete_plan=0`, and the existing proved process,
  handle, output-discard, lifecycle, and private-tree cleanup: the selected
  Runner did not launch, so a fresh exact-current manual Receipt may be used; and
- marker `2` plus one complete version-1 terminal graph whose observation has
  `route=runner`, `launch_state=launched`, `outcome=pass`, null reason,
  `complete_plan=1`, equal positive planned/total/completed step counts, and
  null failed ordinal: that observation is the sole qualifying Runner basis.

For either marker-2 terminal case, the graph must have exactly one resolution,
attempt, cleanup event, observation, Runner Evidence Reference, and
`runner_observation` verification-criterion link for the exact current
generation. Ownership; current Contract and authority/criterion digests;
target, capture, manifest, target-material, plan, implementation, and policy
identities; parent IDs; idempotency/source digests; event-to-observation link;
and the existing closed process-result matrix must all validate. For a live,
non-done Task, the qualifying pass additionally requires the exact currently
installed manifest-bound Runner implementation identity and current-OS policy.
Those live comparisons belong to service preflight, not a SQLite trigger or
historical replay. A pending intent, cleanup-only predecessor, missing
or duplicate member, eligibility mixture, stale generation, target or Contract
drift, malformed graph, or any structurally valid terminal other than the exact
closed no-launch fallback or qualifying pass is not a manual fallback and blocks
completion for that selected marker-2 basis.

The replacement verification-basis cycle guard retains the version-zero
legacy arm. Its native version-one arm admits exactly one of:

- the existing manual specified/full-pass Receipt or trimmed-empty
  `not_required` basis while the Task marker is `0`;
- the existing specified/full-pass manual Receipt basis while marker `2` has the
  exact closed no-launch terminal graph above; or
- a specified verification expectation, null Receipt, kind
  `runner_observation`, and nonnull observation pointer equal to the exact
  stored qualifying-pass graph above.

The replacement evidence-basis cycle guard retains the legacy source-19
Bundle relation and requires every native cycle to reference its unique
same-owner/same-ordinal Bundle. It additionally requires cycle and Bundle
`verification_basis_kind` and Runner pointer to be identical. A source-21
caller/not-required Bundle must match one of the two manual cycle arms; a
source-21 Runner Bundle must match the qualifying-pass arm. Its Runner
observation pointer must select that exact current-generation observation.
These guards preserve the existing deferred Bundle-to-cycle relation and add no
cycle-table rebuild.

The two cycle guards validate only SQLite-stored tags, parents, pointers,
captured identities, and graph relations. They never inspect the filesystem or
current package manifest. Before inserting a new Runner-backed completion, the
service selector owns current package inspection and equality with the captured
Runner implementation identity and policy. A valid done cycle, including one
restored by recovery, instead revalidates identity equality wholly inside its stored graph
and Bundle and is not rebound to the currently installed implementation or OS.

The manual writer creates only Task marker `0` and source-21 Bundle-v2
`caller_attestation|not_required` rows. The Runner target-set service alone may
create marker `2` with a gate-eligibility-version-`1` graph. Shared schema-v21
readers, Viewer, backup, and recovery validators understand both shapes. A
manual completion writer encountering marker `2` fails closed with no cycle,
Bundle, event, Evidence, Viewer, or backup mutation and never reinterprets that
state as ordinary fallback. An explicit fresh target generation is required
before marker-0 manual behavior resumes. This is a service-level selection
boundary and adds no migration or DDL.

The schema-v21 public gate adapter consumes only a fully validated Task and the
internal basis selector. It retains the exact Receipt-era
`verification_evidence` JSON shape and keeps subject, counts, and recent rows
Receipt-only. For a live, non-done Task, marker zero delegates unchanged to the
manual Receipt arm. For marker two, basis freshness precedes outcome: any
structurally valid but non-current graph,
pending graph, or cleanup-only graph maps to `evidence_basis_stale`. Every
exact-current structurally valid terminal other than the exact closed no-launch
fallback or qualifying pass maps to `verification_receipt_blocking`; the exact
closed no-launch delegates to the manual arm, and the exact qualifying pass maps to
satisfied with both public nullable gate fields null. Receipt-add
accepts marker two only for that exact-current no-launch fallback; every other
marker-two branch returns `evidence_basis_stale` before uniqueness. Completion
applies the same branch result before review sufficiency.

A valid done version-one cycle takes precedence over the live matrix. The
adapter revalidates the stored cycle/Bundle arm and replays its manual or Runner gate
projection; a Runner arm is satisfied with both nullable gate fields null only
when its captured stored graph/Bundle identity matches. It never compares a
done arm with the currently installed implementation or current-OS policy, and
this historical read does not authorize a new live Runner completion. Existing argument,
Task/status, expectation, target, generation, and capture checks retain their
order before the applicable selector. Active service preflight returns the
existing `package_core_modified` or `package_status_unknown` result when it
cannot establish the current installed identity; after successful package
inspection, an identity mismatch is stale before outcome mapping.
Malformed Runner/Task storage remains `project_state_unreadable`, malformed
cycle/Bundle history remains `completion_history_inconsistent`, and malformed
Receipt storage remains `invalid_verification_evidence`; no new public error or
field is introduced.

Contract revision, verification expectation or criterion, authority snapshot,
review-target tuple or generation, artifact manifest, plan/selected-entry,
target material, implementation/policy identity, reopen, or retarget drift
prevents an earlier Runner graph from being current. Existing invalidation and
reopen paths clear marker `2` together with the current target and require a
fresh generation; they never update or delete an immutable Runner row, cycle,
Bundle, Reference, link, or historical completion basis.

### Migration, Reentry, And Preservation Algorithm

A v20-to-v21 migration is admitted only from a complete schema-v20 database
that passes the exact current schema-v20 object, Task/Contract, Evidence,
Bundle, Runner audit-graph, `quick_check`, and foreign-key validators. This
includes only the current empty, pending-intent, restart-cleaned, and complete
terminal audit shapes. Thus all predecessor Runner eligibility values and Task
markers are zero and all cycle/Bundle Runner pointers are null. A v20 marker
with any v21 widened table or replacement-trigger definition, a v21 marker with
any v20 definition, a temporary-name collision, partial owned state, known
later marker, integrity or foreign-key failure, or an already gate-eligible row
is rejected before any database, sidecar, backup, Evidence, or Viewer write.

The migrator requires `foreign_keys=ON` and `legacy_alter_table=OFF` on entry,
then sets foreign keys off and legacy alter on before `BEGIN IMMEDIATE`. The
legacy setting prevents SQLite table renames from retargeting the unchanged
foreign keys held by completion cycles, Bundle members, sandbox events, or the
other rebuilt tables. It uses only these exact temporary table names:

```text
completion_evidence_bundles_v20
verification_runner_resolutions_v20
verification_runner_attempts_v20
verification_runner_observations_v20
```

Inside the one transaction it snapshots all existing business tables on their
ordered schema-v20 columns and the normalized owned/unrelated object inventory;
drops the two cycle guards; renames the four old tables; creates the exact v21
resolution, attempt, observation, and Bundle tables; copies every row by the
complete unchanged ordered column list; drops the four old tables; and
recreates the exact owned indexes, immutable triggers, parent guards, and cycle
guards. Parent-before-child creation and copy order is resolution, attempt,
observation, Bundle. The temporary tables are dropped child-before-parent:
Bundle, observation, attempt, resolution.

Source admission, migration, and reentry use this closed extra-object matrix:

| Extra object | Complete-v20 admission | Successful migration 21 | Rollback | Exact-v21 reentry |
|---|---|---|---|---|
| unowned index/trigger attached to `completion_evidence_bundles` | reject before any write under the existing v20 Bundle rule | not reached | not applicable | reject |
| unowned index/trigger attached to `verification_runner_resolutions`, `verification_runner_attempts`, or `verification_runner_observations` | admit under the existing v20 unrelated-extra rule | retire with the old rebuilt table; never replay its DDL | restore with the old table | reject |
| unowned object attached to `verification_runner_sandbox_events` or another table that migration 21 does not rebuild | admit when the existing v20 unrelated-extra rule admits it | preserve its row/SQL unchanged | preserve | preserve when otherwise valid |
| unrelated standalone table/view/index/trigger | admit when the existing v20 unrelated-extra rule admits it | preserve its row/SQL unchanged | preserve | preserve when otherwise valid |

This matrix does not broaden complete-v20 admission. In particular, migration
21 never consumes the Bundle-attached residue that only a complete-v19 source
could have retired during migration 20. For the three rebuilt Runner tables,
transaction rollback restores every admitted attached object even though a
successful migration intentionally retires it.

Before inserting the marker, the migrator proves exact projection equality for
every pre-v21 table on its original ordered columns, exact IDs and row counts,
unchanged source-19/source-20 Bundle payload bytes and digests, expected
owned-object SQL/counts, absence of temporary tables and retired attached
residue, preservation of unrelated standalone objects, `quick_check`, and an
empty foreign-key check. The version-21 marker is the last mutation. Full
schema-v21 and stored-state validation follows before commit. Any injected or
organic failure rolls back every rename, row, trigger, object, and marker. The
`finally` path restores `legacy_alter_table=OFF` and `foreign_keys=ON` and
verifies both; rollback promises logical schema/data identity, not byte-identical
SQLite files.

Fresh schema v21 produces the same final owned SQL and contiguous markers
1 through 21, with empty Runner tables and no invented basis. Exact v21 reentry
is validation-only: it creates, deletes, rewrites, upgrades, or repairs nothing.
Migration never changes eligibility `0` to `1`, Task marker `0` to `2`, adds a
cycle/Bundle Runner pointer, creates a Runner Reference/member/link, or
synthesizes qualification from a schema-v20 audit observation.

### Bundle, Evidence, Viewer, Backup, And Recovery Compatibility

Bundle-v2 serialization remains domain
`taskgov-completion-evidence-bundle-v2\0` and the existing sorted compact key
set. The `verification_basis` object remains its exact four-key object. For a
Runner arm, `runner_observation` is the existing sanitized Runner source
projection with exactly these keys and no others:

```text
observation_id, gate_eligibility_version, route, reason, outcome, launch_state,
complete_plan, total_step_count, completed_step_count, failed_step_ordinal,
started_at, finished_at, duration_ms, cpu_time_ms, peak_job_memory_bytes,
total_process_count, plan_blob_object_id, plan_raw_digest, plan_id, plan_version,
plan_semantic_digest, runner_implementation_version,
runner_implementation_digest, runner_policy_digest, runtime_digest,
sanitized_result_digest
```

The projection is recomputed from the exact observation/resolution graph and
must equal its existing Reference source projection and digest. It never adds
stdout/stderr, command/argv, environment, exit code, exception, credential,
absolute/private path, raw plan, raw target, or debug text. The existing Runner
Reference and verification-criterion link become the Runner Bundle's bound
evidence members; they are reused, not duplicated or caller-authored. Manual arms
keep `runner_observation=null` and their existing members. Preserved Bundles are
never resealed.

The same projection retains the policy and measurement shape defined by the
[Runner accounting owner](runner-execution-specification.md#runner-policy-and-accounting),
including explicit unmeasured values. Stored graph validation keeps its closed
native policy set; the separate
[standalone Evidence compatibility rule](evidence-specification.md#canonical-evidence-bundle-and-index-formats)
does not widen database admission. Backup, recovery, and historical Viewer
reads validate captured meaning without using the current OS as a new basis.

Evidence Index remains v2 with domain `taskgov-evidence-index-v2\0`; a native
schema-v21 Bundle still has `bundle_format_version=2`. Publication remains
query-only capture, Bundle-first/index-last, atomic replacement, and SQLite as
the sole authority. Viewer snapshot remains v4 and expands source compatibility
only from v5-v20 to v5-v21. The v21 reader validates the complete tagged graph,
Bundle members and sanitized projection, then discards all Runner-only fields;
the public Viewer field/UI allow-list, CSP, text-only DOM rule, artifact cap,
and generation behavior do not change.

At the schema-v21 boundary, setup creates v21, migrates complete v1-v20 sources,
and treats v21 as validation-only state; current setup continues to v22 as above.
Managed backup publication-retention suffixes
remain `r<1-20>`; source-schema 21 admission does not change that independent
retention field. All other filename, identity, locking, staging, validation,
and publication rules are unchanged. A
v20 primary or backup receives its normal pre-migration managed backup before
migration 21. Recovery validates each candidate at its declared schema, may
stage-and-migrate a complete v20 candidate to v21, and validates a v21 candidate
without migration. The private Runner tree remains outside SQLite backup;
restored pending intents keep the existing no-relaunch, cleanup-only recovery
rule. A completed historical Runner basis is self-contained in its validated
SQLite graph and Bundle and never depends on a mutable plan file or private
attempt tree.

Schema-v21 stored Tasks use the complete schema-v20 Task, Contract-pointer,
privacy, and relationship validator unchanged, including the exact 1,000-code-
point `verification` capacity. Managed recovery applies the same candidate-
local rule to source schemas 18 through 21: only that field's privacy or
capacity failure rejects one candidate locally. Wrong storage class, cross-
field or relationship failure, another Task fault, or any Runner/Bundle graph
fault remains whole-set fatal.

No reverse migration exists. Rollback to an older package requires restoring a
matching package, database, managed-backup set, Evidence index/Bundles, and
Viewer artifact from the same accepted pre-migration generation; an older
binary must reject schema v21. Invalid, partial, foreign, stale, over-bound, or
privacy-unsafe candidates fail closed before publication and preserve the
last-good canonical database, backup ledger, Evidence index, and Viewer. Each
failure uses its existing sanitized schema, project-state, Evidence, Runner, or
service owner; schema-v21 representation adds no public error code or raw
diagnostic field.

Schema-v21 verification covers normalized DDL and the unchanged 35/42/59
inventory; fresh v21; exact v20-to-v21 row/object/projection preservation;
marker-last rollback at every rebuild stage; same-version no-write reentry;
hybrid rejection; Bundle-attached pre-write rejection; rebuilt-Runner-table
attachment retirement and rollback restoration; sandbox-table and standalone-
object preservation; version-0 audit and version-1 structural graphs; manual-only
marker-2 completion rejection; Bundle-v2/Evidence-v2/Viewer-v4 sources v5-v21;
managed backup `r1-20`; recovery and rollback matching; privacy deny-list;
schema-v21 Task `verification` 1,000/1,001 boundaries; source-v21 candidate-
local privacy/capacity rejection with later-candidate selection; set-fatal
handling for every other Task/relationship/Runner/Bundle fault; and public
schema/setup activation without a new CLI leaf or Skill/Runner/gate behavior.
