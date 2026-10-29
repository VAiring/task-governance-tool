# Setup And State Operation Implementation Design

This document owns the fixed state resolver, identity/binding/relocation, and
setup/Doctor/backup/maintenance structure delegated by the
[implementation design](design.md). Product behavior belongs in the
[Setup and state operation specification](setup-state-specification.md).
Shared [runtime ownership](design.md#runtime-module-boundaries),
[connection/transaction rules](design.md#journal-and-connection-rules),
[migration](database-design.md#migration-sequence),
[current persistence](database-design.md#schema22-reservation-cleanup-design),
[privacy/failure rules](design.md#privacy-safety-and-failure-boundaries), and
[global test design](design.md#validation-and-test-design) retain their owners.
[Stored Task validation](task-operation-design.md#shared-stored-task-rowbatch-validator),
[Evidence publication](evidence-design.md#schema-v19-bundle-and-evidence-publication),
and [Viewer publication](viewer-design.md#snapshot-and-publication) remain directly
coupled responsibilities. The [authority index](authority.md) routes shared
contracts without duplicating them here.

<a id="fixed-state-resolver"></a>

## Fixed State Resolver

One validated governed project and one physical package determine:

```text
state root       <governed-project>/.taskgov
SQLite diagnostics <state-root>/diagnostics/sqlite
Common diagnostics <state-root>/diagnostics/logger
transition lock  <state-root>/taskgov-state.lock
fixed root       <state-root>/current
database         <fixed-root>/taskgov.sqlite
numerical usage  <fixed-root>/taskgov-usage.sqlite
usage index      <fixed-root>/usage/index.json
usage snapshots  <fixed-root>/usage/snapshots
usage lock       <fixed-root>/usage/taskgov-usage.lock
managed backups  <fixed-root>/backups
backup runtime   <fixed-root>/backups/runtime
Evidence index   <fixed-root>/evidence/index.json
Evidence baseline <fixed-root>/evidence/publication-baseline.json
Evidence bundles <fixed-root>/evidence/bundles
Evidence lock    <fixed-root>/evidence/taskgov-evidence.lock
Viewer           <fixed-root>/viewer/task-viewer.html
Runner root       <fixed-root>/verification-runner
Runner lock       <fixed-root>/verification-runner/taskgov-verification-runner.lock
Runner attempts   <fixed-root>/verification-runner/attempts
Runner quarantine <fixed-root>/verification-runner/quarantine
Review waits      <fixed-root>/review-wait/<sha256-reservation-id>.sqlite
Wait send state   <wait-store-filename>.direct.sqlite
Wait dispatch lock <fixed-root>/review-wait/dispatch-<sha256-parent-UUID>.sqlite.lock
```

The resolver returns canonical paths, the in-memory governed root/hash/display
observation, stored identity/binding when available, source schema, layout
state (`missing`, `fixed_current_v1`, or `legacy_projects_v1`), binding state
(`unbound`, `matching`, or `relocation_required`), and an optional deep
setup-only recovery/legacy or layout-migration observation. None of its raw paths or path hashes
crosses a formatter.

Review-wait association, request and lock-only dispatch paths are derived by
this same resolver; their operational protocol is owned by
[Review/completion design](review-completion-design.md#host-owned-review-waiting).
No dispatch database, alternate state root or core migration is introduced.

The backup-runtime path belongs to the same fixed resolver. Its bounded
attempt record and locks follow the
[asynchronous worker boundary](#asynchronous-backup-worker); they are operational
control only, excluded from managed-backup and recovery evidence. Ordinary
state inspection does not create or repair them.

`CanonicalStatePaths.sqlite_diagnostics` and `debug_logs` derive the separate
fixed diagnostic directories. They are outside `current` and core backup/recovery;
its explicit opt-in and publication belong to the
[logging contracts](specification.md#common-debug-logger).
The logger does not initialize or repair the state layout.

For the Runner subsystem, `CanonicalStatePaths` derives the four fixed paths above
and `DatabaseTarget` carries them to the parent service. Internal test targets
derive the same names beneath their injected database parent. No service,
repository, lifecycle, or process module reconstructs the fixed root, and the
layout is created only after an exact Runner route has passed pre-T1
preflight. The lifecycle owner continues to require the existing physical
fixed root as `Runner root.parent`.

Fixed-primary normal consumers validate only the authoritative primary and
derive canonical artifact targets. Setup, recovery, transition, and legacy
discovery use the deep bounded inventory projection. The caller selects the
projection mechanically; it is not a CLI or LLM choice. Every DB-backed leaf
uses the same resolver observation. Feature code may not reconstruct a
project ID or state path.

Task and review/verification adapters select storage's internal
[operation-local boundary](database-specification.md#operation-local-admission).
Completion/check and review snapshots supply their admitted Task ID for own
review storage; Packet and retained hook/event snapshots validate that Task
inside the existing read once selected. Only activated fixed-primary inspection
forwards these choices. Physical integrity/foreign-key checks, package/layout,
identity/binding and structural admission retain their scope. Setup, doctor,
recovery and legacy/transition inspection remain global.

A reviewer hook may still carry its one call-local `DatabaseValidationReuse`
object through existing admission signatures. Operation-local reads retain
only its physical device/inode continuity pin, reject a known replacement,
and discard any global-validation proof. They neither hash the database nor
skip physical, schema, binding or selected-data validation. No rows, connection
or permission are cached, and the object is cleared on failure and hook exit.

The internal optional proof remains applicable only to globally admitted
retained reads of an activated fixed primary. On Windows it compares complete
primary bytes and physical/layout identity before reusing successful global
validation; changed bytes require full validation. Other platforms validate
globally each time without opening a separate descriptor, whose close could
cancel SQLite's process-wide POSIX advisory locks; see
[SQLite's locking caveat](https://sqlite.org/howtocorrupt.html#posix_advisory_locks_canceled_by_a_separate_thread_doing_close_).
Local admission never consumes or publishes this certificate. Neither mode
infers validity from size, mtime or `data_version`, shares a proof across
operations, or changes the physical/native and operational-store checks at
existing effect boundaries. There is no real-host latency or timeout guarantee.

`CanonicalStatePaths.usage_database` owns the numerical sibling path. It is not
part of core admission or core recovery inventory. Only the explicit outer
`run_setup` entry adds independent usage initialization/validation after the
core result, never private separation-candidate setup. Its nested `usage`
result does not alter the core ordered stages, success or rollback boundary.
Before a preview's core initialization/relocation is admitted, numerical status
is `pending_core_setup`, not a guessed binding or claim that a file is absent.

`canonical_state_paths(skill_root, repo=...)` takes the validated governed root
explicitly. Normal CLI dispatch forwards both `ProjectScope.canonical_repo` and
`ProjectScope.skill_root` from successful structural preflight; absent scope
fails closed before resolution. It never reintroduces the raw relative repo
argument. Both path derivation and `observe_current_root` use
`storage.canonicalize_repo`, which expands the user prefix, makes the input
absolute without collapsing `..`, then resolves nonstrictly. This avoids the
Windows relative-input access-denied fallback duplicating a directory while
preserving existence-independent `canonical_path_v1` and normal resolution
semantics. Stored identities, hashes and binding generations are unchanged.
`package_state_paths(skill_root)` is limited to the old migration
source and marker. `DatabaseTarget` transports the generated paths while its
actual physical Skill root continues to own config/templates. Private staged
validation receives both roots explicitly; it never infers Skill ownership
from the candidate's parent. Activated layout remains `fixed_current_v1`;
layout separation is an orthogonal setup-only observation, not another schema
or normal-path storage mode. Before activation the outer resolution has no
ordinary target. Only setup receives a nested old-source observation.

### Lifecycle Usage Hook

`usage_lifecycle.py` owns the optional host input/registration/collection
composition. Its standalone `usage_hook.py` entrypoint reads bounded JSON stdin,
discards unknown fields and bodies, and emits only neutral `{}`/exit0. It uses
the physical entrypoint package and process cwd, never a caller-selected state
path. The existing ordinary-command structural project-scope preflight precedes
resolver admission and core reads, with runtime/package/ignore checks disabled
as in that command boundary. It rejects unsupported or linked installs before
numerical writes, without adding an integrity scan or Git subprocess. The hook
entrypoint forwards argv to the neutral adapter, which accepts only no arguments
or `--repo <project-root>` matching process cwd after lexical normalization.
The latter passes explicit-repo intent to the same preflight, permitting the
existing source-tree exception without redirecting collection or weakening
layout admission. Invalid arguments are neutral no-ops. Review save retains
its existing explicit-repo boundary. No core transaction is opened for writing.
Setup and the normal Task command loop do not invoke the worker.

`usage_hook_setup.py` owns only definition preparation selected by `setup_features.py` after outer `run_setup`
has produced its core and separate numerical outcomes. Private candidate setup,
ordinary commands and doctor do not call it. It merges the four owned handlers
into project `.codex/hooks.json`, using existing physical-file helpers and
no-replace publication for an absent file; an existing file is atomically
replaced after rechecking its observed bytes/identity. Equal semantic content
is never rewritten. This is not a database transaction or a trust writer.
Project-inline TOML is read only to avoid a second usage collector; ambiguous
manual definitions are preserved for review, not parsed as shell programs.
The nested `usage_hooks` result and text notice describe preparation and the
user's remaining trust action independently of core success. Failure is
non-gating and does not rerun or roll back core or numerical setup.

`registered_participants` in the attribution repository reads committed owner
acquisitions and actual Receipt-session bindings. `UsageRepository` exposes the
existing session/source registry and best-effort same-caller registration used
after successful bound review save. Save preserves its original response even
when registration fails. The conditional wait-decision helper uses the same
best-effort actual-caller registration boundary; its schema-4 marker and relation
belong to [wait attribution](database-design.md#review-wait-decision-structure).
Read-only review display/material remain unchanged. Registration itself retains
no source path and attaches no database to the core transaction. The separate
schema-8 locator repository stores bounded location hints as described in
[numerical persistence](database-design.md#numerical-collection-persistence).

`usage_locator_collection.py` composes candidate capture, pending admission,
direct collection and residual inventory. `usage_locator_admission.py` reads
the one held Task selected by the parent ownership index, its current preparation
and bounded collected-answer slots. It records an exact native/assignment basis,
never Packet or answer bodies. Later admission checks the exact existing
Receipt/source relation before considering current Task state, so an already
registered completed Task remains eligible. Confirmed native cancellation or
basis change revokes a candidate; missing stores or changed same-basis slots
remain unknown for possible retained-original registration. It performs no core
write or all-Task/preparation-history enumeration.

For a hint whose session is absent from the numerical registry, the legacy
participant-history query runs only in residual inventory, after known direct
work and with the remaining event budget. If this snapshot admits the session,
its hint is saved and attempted before directory discovery. No short preliminary
history query or repeated query is added. Publication retains its fresh guards.

The lifecycle's resolver and core readers use operation-local physical/schema/
binding admission rather than whole-project review-history validation.
Participant discovery and each fresh publication core read validate the ownership
and completion-cycle history they consume; the usage repository separately
validates actual Receipt/session/collected-answer relationships for its shared
capture basis. Task detail keeps that common basis check as well. These checks
are not reusable across connections, and the final publication guard still opens
fresh admitted core and numerical reads.

`usage_sources.py` locates only registered session IDs in the legacy
`rollout-<prefix>-<session-uuid>.jsonl` and segmented
`rollout-<prefix>-<session-uuid>_<segment-uuid>.jsonl` filenames in the standard
dated sessions and flat archive layouts. It recognizes the two-ID suffix before
checking registration, so a registered ID in the segment position cannot select
an unrelated session. Coexisting segments retain separate source cursors and
use existing response deduplication and immutable snapshot successors; no
attribution or lifecycle change is needed. Directory inventory is
not session admission; links/junctions are skipped, unrelated headers are never
read, and the versioned adapter verifies each candidate before registration.
Explicit host hints can locate the invoking registered source within the same
allowed roots. Missing known sources retain fixed gaps; unsupported layouts do
not authorize wider discovery. Source bytes are read outside all writers.

Before inventory, the lifecycle retains the admitted current hint and checks up
to four pending candidates. Each metadata stage receives at most one second or
one quarter of the remaining event allowance, whichever is less; these are
sub-deadlines within the original budget. It then selects up to eight registered
locators by indexed current-root queue order, plus a direct lookup of the current
hint if absent. A revision CAS rotates the candidate before file work, preserving
fairness across interruption. New hints enqueue at the tail and existing hints
do not reset queue order. Candidate rotation is independent of the numerical
cursor's successful ingest watermark. Direct work reserves up to four remaining
ingest slots for inventory; the total allowance remains sixteen. These bounds
do not guarantee progress after perpetual budget exhaustion or missing events.

`usage_sources.py` reconstructs retained relative paths only for matching current
root fingerprints and validates physical entries afresh. A recognized session
basename also permits one exact flat-archive lookup. It opens no unrelated
header. Coexisting or moved copies get distinct source cursors, with existing
response-key deduplication. A failed optional archive lookup cannot prevent the
original candidate attempt.

`locate_slice` resumes residual directory inventory using root/date/entry
ordinals, at most 1,024 newly visited entries and up to fifteen candidate sources
within remaining ingest slots. Discovered eligible locations are saved before
body ingestion. An inventory cycle marks lost known sources only after its
slices finish, excluding only locators matching the current roots whose missing
status is checked directly. Enumeration may reread preceding names;
renames can defer discovery to a later cycle, but unrelated headers are never
opened. `usage_ingest_work.py` shares the event-wide attempt and audit allowance
across direct and inventory phases. Candidate attempts retain the oldest selected
item first. Within
the existing 16 ingest-attempt slots, the invoking participant then receives up
to four priority slots, counting the first slot if it served that participant.
Its exact admitted host hint is first within that allocation; otherwise use its
oldest discovered segment. Further slots rotate among its discovered segments,
repeating a source only after a committed `more_records` result with forward
complete-record or drain progress. Partial tails, replay, errors and no progress
end repetition for that source. Remaining slots visit the other unattempted
candidates in the original fair order, including any other current-participant
segments. There is no extra discovery scan when a hint is missing.

An ingest result supplies its pending state and committed progress separately
from bytes read: rereading a header is not progress. An audit or budget stop
before ingest progress must not consume a page candidate. Failed invalid
candidates advance attempt order without advancing any data cursor. For lifecycle
collection, successful ingest progress or confirmed EOF saves the attempt
watermark in the same cursor/CAS transaction; a deadline immediately after that
commit cannot erase the successful attempt. Other collector callers do not
change attempt ordering. Each inventory page retains an attempt
watermark; already attempted candidates remain covered across partial events.
Ordering and coverage load only the supplied candidates, using their source
primary key. Schema 9's attempt index supplies the maximum watermark in the
existing writer transaction, preserving atomic progress even if the deadline
expires immediately after commit. No all-source attempt-history scan is needed
for these operations; inventory-loss diagnosis keeps its separate scope.
Once all page candidates have been attempted, continuation can advance even if
no single event processed the whole page. An already completed page can advance
without rereading its bodies. No pending candidate is
discarded merely because the event budget ended. Completed inventory cycles
also report `source_unreadable` for registered participants with no admitted
source, including those never located before.

Each known selected source receives at most one audit slice per event before
its ingest slices, so extra catch-up does not repeatedly audit the same prefix
or starve deferred verification. The reported collected-source count is the
number of distinct successfully visited sources, not the number of slices.
Cooperative event budgets are 20 seconds
(1.5 seconds for SessionEnd), measured from collection entry, including payload,
package and state admission. The same absolute deadline reaches the resolver,
participant discovery, collection and publication, with 50 ms SQLite lock waits
and query progress interrupts for these numerical workers, resolver database
inspection and admitted core readers. Expired admission cannot start collection
with a fresh budget. They
do not alter ordinary Task connection budgets or admission checks. Filesystem
operations, one bounded record, current-component validation and metadata passes can still dominate
wall time; there is no universal host-timeout guarantee. The same
compare-and-commit cursor/continuation rule handles interruption, followed by
`refresh_usage` for exact-cycle capture and snapshot-first publication. Its
schema-7 publication cursor, preserved by schemas 8 and 9, continues the rotating history audit across events; a completed
sweep retains explicit deferred-integrity status. It uses
the existing canonical paths above; no daemon, timer, process, model call,
post-commit Task callback or host-settings writer is added. A later same-project
event retries backlog and late records, with pending coverage throughout.

### Separation Record And Publication

`state_separation.py` imports neither resolver nor setup. It owns the canonical
ASCII v1 record at `.taskgov/.state-separation/record.json`, fixed sibling
`source/` and `candidate/`, and the old fixed-primary retirement marker.
The record carries only transition ID, phase (`private`, `fenced`, `activated`),
project ID, optional original layout/schema/binding basis, original source
inventory fingerprint, retained-snapshot digest and prepared-candidate digest.
Those three digests have different meanings: SQLite backup need not reproduce
original database bytes. Fresh preparation has no source basis and persists
its UUID once. Missing preparation digests are permitted only in `private`
and cannot authorize fencing. No arbitrary path or business row is recorded.

The state-root inventory also admits the physical `diagnostics/` directory as
a non-state sibling. It validates the directory boundary without traversing,
copying, deleting or treating its contents as transition/recovery evidence.
Other unknown root entries and non-directory/linklike `diagnostics` entries
retain the existing refusal. The diagnostic sink owns its deeper path checks.

`setup_state_separation.py` owns explicit offline planning and the retained
lock sequence: new-root transition, old-root transition, then source Runner,
Evidence, Viewer and backup locks. All are zero-wait. No SQLite writer spans
copying, validation, projection rendering or directory publication. The
domain-bounded inventory helper composes existing source validators; it does
not enlarge the legacy staging cap or weaken their admission meanings.
For `backups/validation`, it uses the backup record owner's closed bounded
physical inventory, including `.pending.json`, and hashes those recognized
files into the retained and candidate seals. Generic inventory copying preserves
the directory and bytes; neither copying nor sealing enrolls a content proof.
This applies to strict private inventories and recognized source domains alike.
Initial inventory observation takes no lock: an OS-denied read of a held lock
file is a source-read failure; contention detected during zero-wait acquisition
is `database_busy`. Neither outcome authorizes source replacement.
If a supplied relocation confirmation was concurrently activated for the same
project, release all transition locks before passing the unchanged invocation
to current-state setup validation. This preserves the existing used-token
response and one ignore preflight; it is not a general retry or source fallback.

Before source preparation or relocation confirmation, the coordinator uses
the existing old-stage inspector and validated cleanup flag to recognize an
unfinished predecessor migration. It preserves that state and reports
`setup_incomplete`, leaving its completion to the compatible pre-separation
runtime before upgrade. It does not invoke the predecessor cleanup primitive
or reinterpret its inventory against the new root. Healthy sources continue
directly; new separation preparation has its own retry rules below.

Setup validates and retains the source, normalizes only its private candidate,
prepares/flushed the marker beside the old state root, and atomically replaces
the validated primary (or publishes no-replace when absent). It publishes the
candidate no-replace and records activation last. Record updates use an exact
validated predecessor and a flushed sibling temporary; retries accept only the
same prepared successor. Ordinary admission requires activation plus matching
marker, validates new state with existing consumers, and never compares a
live DB to its pre-activation digest. Missing/corrupt new primary uses the
existing new-root recovery contract, never retained-source selection.

A retained backup-only source is still the selected immutable recovery
snapshot, not a normalized active primary. Its copied DB must pass the
existing selected-observation validation; the complete retained inventory is
sealed separately. On retry, the retained-snapshot reader checks that seal,
the recorded identity/schema/binding basis, and the existing physical and DB
validators without treating the snapshot's pre-publication backup rows as an
active-primary repository. Only the private candidate is reconciled, then
fully checked by the unchanged staged resolver. Ordinary and candidate
admission never use this retained-source reader. Recovery selection and the
complete original source set remain pinned through the final pre-fence check;
post-plan drift retains `setup_restore_failed`.

Missing-marker repair validates the retained source digest and the old domains
without comparing the active DB to historical bytes. Fresh origin permits no
old generated source; fixed origin compares retained recognized artifacts with
the old fixed inventory excluding only the replaced primary; legacy origin
compares the sole original legacy source with its recorded fingerprint. An
additional project candidate or old-stage residue is a conflict. Repeat the
observation under the existing transition/domain locks before publishing the
marker. This setup-only check adds no ordinary-command old-tree scan or new
record format, and does not select among competing old databases.

An unsealed nonempty partial preparation is preserved and reports
`setup_incomplete`: recognized names alone cannot prove its original bytes.
It is not automatically deleted, regenerated or blessed as a complete
candidate. Empty owned preparation may proceed. After sealing, retry requires
the recorded source/candidate observations. Before fencing, preview compares the
already inspected original-source inventory with its recorded fingerprint;
write setup repeats that comparison under locks. Once fenced, the retained
snapshot uses its own seal, never the original database's byte fingerprint.
The sealed-candidate reader retains ordinary supplied-token validation for a
nonrelocated source and validates the already-authorized binding result for a
relocated source. New backup-policy options do not rewrite a sealed candidate:
after exact activation and releasing transition locks, the coordinator uses
ordinary setup's scope/identity checks and maintenance repository configuration
to apply only supplied fields. It appends the existing `maintenance_configure`
stage to the preview and durable result; failure preserves activation and is
resumable through ordinary setup. Equal or omitted policy remains unchanged.
An observed matching marker or published candidate may be ahead of its phase
record; setup recognizes that exact completed step. The retained source is
outside routine pruning and has no automatic cleanup. These are process-crash
reentry guarantees within the offline prerequisite, not power-loss or online
migration guarantees.

<a id="stable-project-identity-binding-and-relocation"></a>

## Stable Project Identity, Binding, And Relocation

### Schema V14 Identity

Fresh setup creates `project_id=tg_project_<uuid4 hex>`,
`identity_scheme=uuid_v1`, binding generation 1, and reason `fresh_setup`.
Migrated databases keep their legacy sanitized-name-plus-12-hex
`legacy_path_v1` ID. Durable business rows never change project ID.

`project_meta.canonical_path_hash` is the current binding, not identity.
Schema v14 also stores:

- `binding_generation >= 1`;
- reason `legacy_migration`, `fresh_setup`, or `confirmed_relocation`;
- canonical binding timestamp;
- bounded sanitized display name; and
- the exact cleanup state triple: either pending `0` with null inventory/hash,
  or pending `1` with canonical inventory and its lowercase SHA-256.

`project_path_binding_history` is append-only and keyed by project plus
generation. Generation 1 has no previous hash; each later entry's previous
hash equals the prior current hash and has reason `confirmed_relocation` plus a
token digest. Current metadata must equal the maximum history row. Triggers
prevent identity/creation changes, project deletion, history update/delete,
and invalid cleanup triples. Validation checks consecutive lineage,
scheme-specific IDs, lowercase 64-hex hashes, canonical timestamps, bounded
display text, and canonical cleanup inventory.

`project_binding_repository.py` compares identity, expected generation, and old hash
inside `BEGIN IMMEDIATE`, rejects a same-hash change and signed-64-bit
generation overflow, inserts generation `N+1`, updates the current binding,
and increments Viewer source generation in the same transaction. A missing or
overflowing Viewer row rolls the whole binding change back. The token digest,
not token text, is retained in history.

### Bounded Legacy And Recovery Discovery

Legacy inspection runs only when fixed primary and higher-precedence fixed
recovery are absent. It scans only direct entries under `state/projects`,
consumes at most 64, and rejects an unknown entry, a 65th entry, link/reparse
component, escape, invalid candidate, or multiple candidates without creating
or writing anything.

The candidate validator checks:

- exact legacy directory/database names and one project row;
- contiguous schema and required objects without later markers;
- operational journal mode, quick check, and foreign keys;
- identity/scheme, directory equality, and v14 binding lineage;
- every recognized managed backup as the same identity/scheme with a lineage
  prefix of the mechanically newest source;
- coherent schema-specific backup pointer/rows/files, with at most 21
  retained-plus-in-flight generation identities;
- canonical database, backup, Evidence, Viewer, lock, and recognized temporary paths; and
- at most one bounded regular temporary for each exact restore, backup, and
  Viewer/Evidence temporary-name grammar.

Recovery classification retains a structural/set phase and a content eligibility
phase. The shared immutable-backup inspector observes each recognized candidate
once, using the content-bound record below only for certified internal checks.
Physical, identity, lineage, metadata and whole-set checks still run against the
mechanically newest structural head. Without a reusable record it calls the
storage-owned exact validator with that candidate's source schema. The validator returns normally
or raises a sanitized internal privacy/capacity rejection; every other storage
failure remains structural and set-fatal. The current implementation accepts
at most 500 characters through schema v17 and 1,000 at schema v18-v34. The source
schema is selected before migration or recovery publication; no recovery-only
limit or migration laundering exists.

For schemas 29, 30, 31, 32, 33 and 34, both resolver discovery and managed-backup selection
dispatch to their version's validator: `schema_review_continuation`,
`schema_review_answers`, `schema_criterion_link_collector` or
`schema_session_slot_index`, `schema_own_review_indexes` or `schema_viewer_projection`, respectively,
with `validate_storage(recovery=True)`.
Each validator finishes Bundle and continuation graph checks before returning a deferred
Task-verification rejection; a local rejection cannot hide structural damage.

For schema 34, the source observation also retains typed Viewer tracking state.
After source comparison, private restore normalization advances its source and
invalidation generations once, rotates the database epoch and clears both
derived publication tuples. The normalized expected observation permits only
that delta; the original backup and business rows remain unchanged. Publication
then revalidates the candidate. Ordinary reentry never repeats this rotation.

The structural phase also validates each candidate as the immutable
pre-publication repository snapshot for its ordered physical prefix. One
set-wide generation registry requires every occurrence of a generation ID in
a filename, embedded row, or maintenance pointer to carry identical complete
metadata. A v11+
candidate has its pointer at its last embedded generation row, exactly its own
generation is file-only in that prefix, and bounded row-only generations are
strictly older. A v10 candidate has no rows and its optional pointer is older
than the candidate. With a retained physical predecessor, that pointer equals
the predecessor's complete metadata; only the first retained candidate may
refer to an older generation ID absent from the complete physical candidate
set. Candidate schema versions are not required to be monotonic because an
older fallback can produce a new pre-migration backup after a newer rejected
head.
The storage layer owns one raw SQLite integer-storage validator used by the
resolver's generation-row and maintenance-pointer reads and by ordinary
maintenance/managed-generation repository reads. It accepts only
`type(value) is int` before semantic range validation; present `REAL`, `TEXT`,
`BLOB`, or other non-`INTEGER` values are never normalized with `int(...)`.
Resolver and setup paths translate that defect to the structural
`project_state_unreadable` result before selection or publication, while the
same helper keeps ordinary reader and setup-reentry behavior aligned.
Candidate-derived rollback-journal, WAL, and SHM names, including filesystem
case aliases, are rejected regardless of size. The storage classifier scans
all Task rows so a later malformed SQLite value cannot be hidden by an earlier
local rejection; privacy wins over capacity only after that structural scan
completes.

Each internal managed-backup observation includes metadata, source schema,
stored project/binding lineage, content eligibility, path, and the regular
file's device/inode/size/mtime identity and observed content digest. Fixed and legacy backup-only
resolution retain the complete ordered observation set, select the newest
content-eligible candidate at the current binding, and expose both the
selection and full set to setup. Setup requires its recovery selector to match
that exact selected observation, then compares the complete observation again
while holding the artifact lock. Any candidate or classification drift is a
restore failure; there is no under-lock reselection.

Content eligibility also requires exact identity scheme, binding generation,
canonical path hash, and full binding lineage equality with the structural
head. Earlier valid lineage-prefix artifacts remain structurally observable
but cannot be selected across a binding generation, including an A-to-B-to-A
path history.

Fixed recovery carries that immutable ordered inventory into the restore
service. The service checks the complete set at entry, before private
repository normalization, and immediately before its no-replace canonical
link; both resolver results must equal the planned observation and the
newest eligible member must still be the planned selection. Before
normalization, the private SQLite copy must exactly retain the selected
candidate's project/binding, source schema, generation rows, maintenance
pointer, and content classification. After normalization it is opened again
through the schema-aware resolver validator and must have every required
object plus the exact all-artifact row set and mechanical-head pointer. Legacy
backup-only publication pins the selected source identity and the same
repository observation, validates both
the source and copied stage, checks every copied backup against its planned
identity, and resolves the complete legacy source again immediately before the
no-replace stage rename. Any mismatch maps to `setup_restore_failed` and the
private stage is removed without publishing fixed state.

The fixed resolver's complete comparison is the last source-set check before
directory/canonical-destination guards and the no-replace link; a duplicate
backup-service scan is unnecessary at that same boundary. Before private
normalization, the backup-service inventory supplies the same content-aware
classification. Legacy restoration retains its service inventory guard.
Healthy matching canonical-primary Setup uses only physical backup inventory
and the primary's generation/pointer envelope. It does not open or hash old
backup contents. Relocation, legacy publication and missing-primary recovery
still require complete candidate observations. In these content-dependent
paths, local privacy/capacity rejections remain nonselectable and structural
defects remain set-fatal; a record mismatch is structural, never a local rejection.

Before private old-layout recovery reconciliation, an older selected copy can
legitimately lack several newer generation rows. That entry carries its exact
planned recovery observation: the complete copied backup set must still pass
the recovery envelope, its newest eligible member must match the planned
candidate and digest, and the independently inspected private database must
match the selected snapshot. Only then may reconciliation import later rows.
Ordinary primary reconciliation keeps its one-in-flight row/file envelope;
subsequent reconciliation after normalization uses that ordinary envelope.

The successful return from `restore_managed_backup` is the durable
publication boundary because it occurs only after the canonical no-replace
link. Setup appends `database_restore` immediately on that return, then checks
the returned schema and reads post-link setup state. Failure in either later
check keeps `setup_restore_failed` but carries the durable restore prefix;
failure before return keeps the prefix empty. The restore primitive always
removes its sibling temporary in `finally`, and a retry observes the canonical
primary instead of reselecting or publishing another candidate.

The recovery-specific backup scan uses the same storage validator and excludes
only its privacy/capacity rejection from selection. A structurally coherent set
with only local rejections has no eligible candidate and reaches
`setup_restore_failed`; corrupt, foreign, unsafe, duplicate, overflow, or
otherwise structurally invalid recognized material retains its specific
resolver result where applicable and otherwise reaches
`project_state_unreadable`. Recovery normalization
keeps every structurally coherent artifact in the ordinary generation
row/file/pointer envelope even when the copied candidate is older; local
rejection never hides or legalizes a missing or mismatched relation. It does
not rewrite rejected artifacts, while later ordinary retention keeps its
existing authority over managed generations.

Focused recovery-boundary ownership is split by behavior rather than accumulated in one
module. `test_m214b_recovery_boundaries.py` owns current fixed-layout,
selection, metadata, and TOCTOU behavior in the integration lane;
`test_m214b_legacy_recovery_boundaries.py` owns legacy-primary, schema-v10, and
mixed-schema recovery in the release lane. Shared mutation helpers live in the
non-discovered `m214b_test_support.py`, while the common tree snapshot helper
captures complete managed-state names, kinds, sizes, and contents for no-write
assertions. New recovery cases extend their owning module instead of restoring
one mixed oversized file. TOCTOU coverage observes the phase order around a
drift injection, proves that the last deep resolver comparison follows the
final shallow inventory refresh, and proves that the canonical publish call is
not reached; it does not freeze a private helper call count.

Other contained names are opaque user material and are preserved. A fixed
primary always wins even when invalid; the resolver never falls back around
it. After whole-set validation, fixed missing-primary recovery selects the
newest content-eligible current-binding generation and requires the
mechanically newest binding head to match the governed root. Earlier
generations may be exact lineage prefixes. A moved backup-only legacy source,
foreign/divergent identity, corrupt primary, or unsupported journal is never
a relocation fallback.

Same-binding legacy primary or backup-only state is setup-publishable to fixed
state. Normal commands report migration-required until that atomic
publication. A primary-backed moved source is read-only
`project_relocation_required`; a moved fixed primary is likewise usable only
through explicit relocation confirmation.

### Relocation Token

`relocation.py` emits an opaque, non-secret token:

```text
tgr1.<unpadded-base64url-canonical-json>.<sha256>
```

Its exact payload binds version 1, project ID/scheme, current binding
generation, distinct old/new 64-hex hashes, source layout, source schema, issue
time, and expiry exactly 900 seconds later. A fixed-current source may be
schema v1-v34; `legacy_projects_v1` is capped at the explicit v14 transition,
while ordinary legacy discovery remains v1-v13 plus that v14 shape. Canonical JSON uses
sorted keys, compact separators, UTF-8, and `ensure_ascii=True`; the checksum
is SHA-256 over ASCII `tgr1.<payload>` and is compared with
`hmac.compare_digest`. The whole token is capped at 2,048 ASCII bytes.
Padding, noncanonical encoding, duplicate/missing/extra keys, bad types,
ranges, or times are rejected.

Preview fixes the clock and is no-write. A valid token requires
`issued_at <= now < expires_at`, but handling first checks structural
validity/checksum and successful-token digest reuse. A successfully applied
token therefore remains `relocation_token_used` after expiry. A changed
source, identity, generation, binding, or governed-root observation is stale;
a matching context is not relocation-required. Rejected token values never
appear in output.

`setup --read-only` may emit the fixed six-key relocation preview containing
the token and expiry but no path/hash. Agents must present that preview and
wait for explicit current approval; they never auto-confirm.
`--read-only --confirm-relocation` is rejected before scope inspection.
Write-mode mismatch without the exact token remains no-write.

### Staged Publication And Cleanup

Ordinary already-activated setup acquires locks in this order:

1. zero-wait project state-transition lock;
2. relevant source/fixed managed-backup artifact lock; and
3. short private or canonical SQLite transactions.

It never holds a SQLite writer while copying, rendering, invoking Git,
publishing a directory, or cleaning legacy files. Normal business commands do
not acquire the transition lock; canonical rebind instead uses SQLite
compare-and-swap.

The predecessor legacy publication primitive remains a bounded compatibility
mechanism for its persisted stage/cleanup inputs, with the following format.
New separation does not invoke this delete-after-publication path; it uses
the separate source retention and record above.

1. Revalidate scope, ignore, destination absence, journal, schema, integrity,
   identity/binding, artifact inventory, and optional token under the
   transition lock.
2. Create exclusive, durably flushed paired
   `state/.current-stage-<32hex>.owner` and
   `state/.current-stage-<32hex>` entries. The compact owner record is at most
   2,048 ASCII bytes and binds version, stage ID, project ID, and source
   inventory fingerprint without a path.
3. Admit only database/optional rollback journal, up to 21 managed backups,
   canonical artifact locks, Viewer HTML, and at most one recognized bounded
   temporary per class; at most 32 regular files are staged, each bounded by
   source database size plus 16 MiB. Unsafe, unknown, duplicate, oversized, or
   unowned residue is preserved and stops.
4. Copy the selected database through SQLite backup and copy only validated
   managed/last-good artifacts. Reconcile backup state inside the private
   stage, apply required migrations and maintenance configuration, apply a
   confirmed binding change, persist cleanup intent, and render/validate the
   current Viewer there.
5. Build canonical inventory JSON
   `{"entries":[...],"v":1}` from 1-32 unique recognized source files. Each
   entry contains only kind, relative POSIX name, lowercase SHA-256, and size;
   sorting is by UTF-8 name bytes, canonical bytes are at most 16,384, and the
   fingerprint is their SHA-256.
6. Validate the complete staged database, history, artifacts, Viewer,
   sidecar absence, and binding.
7. Rename the complete directory no-replace to absent `state/current`, then
   remove the external owner marker. A concurrent destination is never
   overwritten.

A pre-publication crash leaves the source authoritative and only a fully
validated owned residue eligible for bounded setup cleanup. A
post-publication crash leaves fixed state authoritative and legacy cleanup
pending. A port without equivalent no-replace directory semantics must fail
closed.

Pending legacy cleanup derives its retirement directory as
`state/.legacy-cleanup-<sha256(project-id)>`. The persisted canonical
inventory, never directory enumeration, is the allow-list. Each recorded file
must be absent or match kind, size, and SHA-256 at exactly one old/retirement
location. Cleanup first no-replace moves remaining old files, then deletes
verified retirement files one by one and only proven-empty owned directories.
Unexpected or changed content stops; unrelated old content remains. One short
transaction clears cleanup state after the recorded set and retirement
directory are absent. The compatible predecessor runtime's token-free setup
resumes this pending cleanup before the separation prerequisite is satisfied.

Fixed-state relocation performs only the binding transaction, then publishes
the Viewer by its generation contract. A crash or Viewer failure after commit
leaves relocation durable and Viewer due; token-free setup repairs it. It does
not manufacture an immediate backup, so loss of the primary before later
managed publication is an explicit bounded recovery window.

<a id="setup-doctor-backup-and-maintenance"></a>

## Setup, Doctor, Backup, And Maintenance

### Shared Scope Preflight

`project_scope.py` retains root inspection failures until fixed classification.
Root and ancestor metadata use exception-preserving `lstat`, not predicates
that can silently turn an access failure into false. Lexical root construction,
physical-directory observation, strict canonical resolution, and ancestor-link
inspection map missing/non-directory/invalid-path failures to
`invalid_project_root`, including `ValueError` and Windows `winerror=123`
(`ERROR_INVALID_NAME`). A generic `errno=EINVAL` is not sufficient evidence of
an invalid name; other OS errors and normalization `RuntimeError` map to
`project_root_uninspectable`. A failed root observation yields no scope and
never reaches state-path, ignore, or database resolution. The new code is in
the shared structural allow-list and follows the same preflight priority as
root validity, before state/package/ignore failures. Doctor maps it to
`project_uninspectable`; setup and ordinary CLI commands reuse the fixed
message. No exception text, CLI retry, permission change, or new normal-loop
diagnostic operation is added.

`strict_path.resolve_strict_path` owns the bounded internal alternative used
only at the root's strict observation and `state_resolver._require_contained`.
It first calls `Path.resolve(strict=True)`. Only Windows `winerror=5` enters
native observation; other platforms, successful results, and other failures
retain standard behavior. Ordinary DOS input is normalized with public path
functions. Access-zero `CreateFileW` handles (`OPEN_EXISTING`,
`FILE_FLAG_BACKUP_SEMANTICS`, no sharing) supply
`GetFinalPathNameByHandleW(FILE_NAME_NORMALIZED | VOLUME_NAME_NT)` observations
in a fixed 32,768-character buffer, with every acquired handle closed.
The input drive's root must be a direct `HarddiskVolume` root; the normalized
target must share that exact volume component boundary. Only the drive comes
from the input: the suffix comes from native data. Ambiguous ordinary-DOS
suffixes, subdirectory drive mappings, and unsupported namespaces refuse.
Reopening that DOS candidate must yield the identical NT target, and a second
drive-root observation must match. The candidate must also match the existing
nonstrict canonical spelling under Windows case normalization. Any native or
consistency failure re-raises the original standard exception for existing
sanitized root/state classification.

These successive observations do not promise atomic namespace stability or
general drive-alias equivalence. Existing physical/ancestor and containment
checks remain required. `storage.canonicalize_repo`, identity hashing and
SQLite URI construction keep their nonstrict, existence-independent contracts;
Packet validation still performs its single existing public live read. No
private Python resolver hook, global monkeypatch, OS configuration, persistent
cache, path logging, or new caller decision is involved.

Setup and doctor validate a physical target directory, canonical package,
layout, state ownership, and package identity. If the target or an ancestor
has a `.git` marker, `project_scope.py` runs exactly one bounded, shell-free
effective-ignore command before SQLite:

```text
git -C <governed-target> -c core.fsmonitor=false \
  check-ignore --quiet --no-index -- <target-relative-state-directory>
```

The operand is exactly `.taskgov/` for ordinary and self-host layouts, with
forward slashes and directory semantics.
The process uses safe Git environment, null stdin/stdout/stderr, and a
two-second timeout. Return 0 is accepted; every other result is
`state_ignore_required`. No marker means a valid non-Git target and no
process. The governed target is never re-rooted. Normal task/handoff/review
commands perform no ignore process.

### Doctor

Doctor combines:

1. bounded package-manifest inspection;
2. the conditional one-process ignore observation; and
3. at most one journal preflight and coherent project snapshot; and
4. one bounded, read-only backup-runtime observation when that component is available.

It does not claim cross-source atomicity. `project_state` owns layout and DB
readiness. When unavailable, task/handoff/maintenance details use fixed
unavailable objects. Package modified/unknown is advisory even when project
state produces exit 2. Relocation mismatch is a successful
`relocation_required` project-state advisory, never a token. Every recognized
advisory has `suggested_action=continue`; doctor creates no lock, directory,
sidecar, database, event, backup, Evidence, or Viewer.

The maintenance projection adds one fixed `evidence` object beside backup and Viewer with `code`, `due`, `source_generation`, `published_generation`, `last_success_at`, and `last_outcome`; doctor reads stored facts only. The backup runtime has its separate `observed_at`; it never inherits the database snapshot's atomicity. An existing lock can be probed without creation, publication ownership or retained acquisition, and uncertainty is returned rather than repaired.

The coherent project snapshot loads complete Task rows once and validates the
whole batch with the shared stored-Task/Contract validator before computing Task
counts. For a matching current-schema Doctor observation, resolver admission
checks structure and project metadata, then the exact-schema Setup reader owns
the global Runner, ownership, review, note, cycle and ledger row audit on that
same connection. It does not first repeat that row audit in the resolver.
The matching Doctor resolver and its nested Setup reader share successful
physical checks only inside a lexical query-only read scope. Quick, full
integrity and FK are separate results: quick success never replaces the full
integrity check. A private savepoint token, exact connection and write count
guard each reuse; ending/replacing the transaction or intervening writes drops
the successes. Failed checks are never retained. Scope exit releases its tokens
and values, including when the owner already closed the connection. Standalone
Setup/Doctor reads create fresh scopes; direct validators and non-Doctor resolver
observations do not inherit an earlier call's checks. This is no persisted proof
and does not alter diagnostics, recovery or migration gates.
The exact-schema validator privately returns its complete validated Task batch
to the Setup reader, which checks the target project association without
reloading or revalidating those Tasks or repeating cycle/ledger validation.
The batch is consumed inside this read call; it is not an exported certificate,
an input bypass, or a cache across connections, snapshots or Setup stages.
The same composition applies to supported schema21 through schema32 readers:
their private exact-schema entries return the admitted batch only after all
version-specific row, ownership, privacy and physical checks finish. Their
standalone public validators still return no proof, and recovery retains its
existing exception and no-batch behavior. An empty admitted batch is consumed
as empty, not treated as a request to fetch again. Schema20 and earlier paths
have no such returned admitted batch and retain their existing reads. Current
schema33/34 sharing is unchanged. Only the target project association is checked
by the consuming Setup reader; the historical version's validation is not rerun.
Standalone Setup, Doctor and schema-validation entries still perform their
full applicable validation. Older-schema and recovery paths retain their
existing validation and classification. On a
Task fault, doctor maps project state to `unreadable`, every other
project-backed component to `unavailable`, and setup eligibility to false;
the package observation remains independent.

The package inspector strictly parses manifest v1, caps it at 256 KiB and 512
core entries, rejects unknown/duplicate keys, invalid identity/version/origin/
hash/path, traversal/backslashes/absolute paths, casefold collisions, and
entries in excluded regions. It enumerates physical regular core without
following links. Only root `config/`, `adapters/`, generated `state/`,
`__pycache__/`, and `*.pyc` are excluded. Files are streamed with per-file and
aggregate bounds and pre/post identity checks.

Results are `clean`, `modified`, or `unknown`; paths are bounded relative names
sorted and capped to 20. No content, expected/actual hash, absolute path, link
target, or OS error is emitted. The co-located manifest is unsigned and is a
local drift detector, not authentication. Doctor never downloads, repairs,
updates, or compares upstream state.

### Setup Plan And Stages

`run_setup` owns one [common diagnostic scope](logger-design.md#connection-inventory),
reusing an enclosing CLI scope. Matching resolution permits output; fresh or
relocated destinations require the existing successful publication outcome.
No diagnostic DB reread is added. Read-only suppression also covers nested calls.

After core and numerical setup, `setup_features.py` applies only the seven
explicit optional choices and projects saved intent separately from current
configuration. `setup_feature_config.py` owns the closed local choice file and
bounded physical compare-before-replace publication for choices/profiles;
invalid originals are preserved. No SQLite transaction spans these writes.
Runner reuses its existing codec and local-only publisher; no execution layer
is imported. Effort and Viewer reuse their feature readers for validity.
Viewer toggle publication reuses `publish_setup_viewer` against the existing
resolved target. No second setup, migration, Runner or collection is launched.
Review wait stores only its Boolean choice. Its runtime reads that policy on
normal wait, compatible prepare/start and fresh basis checks; Setup performs no
host configuration or operation. The resolver derives its separate operational subtree, which is
excluded from core backup/recovery inventory and never resumes on restoration.

Answer collection and correction use distinct saved Boolean choices.
`review_answer_hook_setup.py` shares bounded physical publication with existing
hook setup, preserving unrelated content and rejecting conflicting owned or
inline definitions. Only explicit collection ON prepares the typed MCP event
handler and appends its tool to an existing server's explicit allowlist. The
bounded TOML adapter validates the whole document, changes only the normal
`mcp_servers.taskgov_review_wait` table's `enabled_tools` array, and reparses to
prove that no other value changed. It preserves text outside that value and
publishes through the same compare-before-replace primitive. Missing allowlists
need no edit; malformed settings, conflicting deny rules and unsupported table
spellings fail closed. No server stanza, launch path, environment or trust is
created. Its separate `answer_collection_setup` diagnostic reports planned
hook/allowlist writes, durable completed prefix and missing/disabled connection
prerequisites, including read-only preview; core success is not host activation.
Ordinary runtime reads never publish configuration; trust and connected host
capability remain separate. `state_resolver.py` derives each collection
store from the retained parent/Task/generation/preparation and optional round,
under the existing operational subtree. It is not core backup material.
Neither Setup nor operational restart sends a correction or resumes a wait.

Feature config application and saved-choice publication are separate. A later
failure rereads observable state and returns an unavailable feature, not a
rollback or success claim. Unrelated features can finish. Saved usage OFF is
checked by `usage_lifecycle` before any log read or numerical write; an invalid
choice file also prevents collection. Missing choices preserve legacy hook
invocation, while fresh Setup does not create definitions without ON. Valid
existing definitions/configs resolve the offer without an automatic choice
write. Host trust stays unknown. The closed I/O and permissions belong to
[optional feature selection](setup-state-specification.md#optional-feature-selection).

Read-only setup returns a deterministic plan for restore, initialization,
migration backup, migration, maintenance configuration, Evidence publication, and Viewer publication
plus effective backup interval/retention. It creates no directory, lock,
temporary, sidecar, SQLite recovery connection, or HTML.
Setup serialization adds `evidence_status`; its ordered write list places `evidence_projection_publish` after maintenance/binding and before `viewer_publish`.

Write setup revalidates scope immediately before each irreversible stage. A
completed stage is not rolled back because a later stage fails; the exact
ordered partial result makes rerun resume. Initialization, migration, recovery,
relocation, maintenance opt-in, and Evidence/Viewer publication use services directly,
not removed CLI subprocesses.

When canonical DB is absent, setup scans only canonical managed backups:

- no managed name => fresh initialization;
- at least one eligible same-project/current-binding generation => newest
  eligible `(published_at, generation_id)` recovery while the mechanically
  newest generation remains the structural head;
- a structurally coherent set whose current-binding candidates are all locally
  rejected only for stored Task-verification privacy/capacity =>
  `setup_restore_failed` without initialization; and
- a structural, identity, binding, lineage, metadata, repository, retention,
  sidecar, or set-envelope failure => the specific resolver result where
  applicable, otherwise `project_state_unreadable`.

Invalid, foreign, linked, or unrelated files are preserved. Once a recovery
candidate plan exists, drift, copy, normalization, or no-clobber publication
failure maps to `setup_restore_failed`. Recovery holds the artifact lock,
revalidates the candidate, copies it through SQLite backup to a
sibling temporary DB, reconciles only supported backup metadata, validates
schema/identity/quick/FK/regular-file state, and publishes no-clobber. A
lexical rollback journal beside a missing canonical DB is rejected before
selection and publication and never opened/deleted. A concurrently appearing
canonical DB is never overwritten.

A supported old recovery migrates normally; current recovery proceeds to
Evidence and then Viewer. Fresh initialization repeats candidate/journal absence under the lock
immediately before creation. Setup never silently substitutes initialization
for a stale recovery plan.

### Maintenance Policy And Managed Backups

`backup_metadata_repository.py` owns the shared maintenance-policy read/seed
and backup-policy configuration, plus managed-generation, pointer, outcome,
and retention writes and their direct record validation. The shared
`ProjectMaintenanceState` retains its Viewer fields; Viewer publication writes
belong to `viewer_metadata_repository.py`. Storage retains schema admission and migration
orchestration, and backup/setup retain physical publication and reconciliation.

Schema v10 has one `project_maintenance` row with immutable one-way
`enabled_at`, backup interval/generation policy, applied retention, shared
backup success/outcome/latest generation, and Viewer success/outcome. Setup
defaults a new policy to 30 minutes and three generations. Public bounds are
1-1,440 minutes and 1-20 generations. Omitted values preserve existing
policy; equal explicit values replay. A policy-only change does no copy/prune,
and a reduced retention becomes applied only with the next successful
publication.

The retained synchronous backup primitive used by Setup and pre-migration
protection:

1. opens a rollback-journal source through the operational boundary;
2. copies with `sqlite3.Connection.backup` to a fresh contained temporary and
   immediately closes the initial source connection;
3. closes the copy, fingerprints its bytes, and completely validates it in an
   independent schema-aware read transaction, then fingerprints again;
4. flushes the copy, publishes its bounded validation record, and validates a
   freshly opened source; a final fingerprint guards copy publication;
5. atomically publishes a canonical generation with fixed
   publication-retention metadata; and
6. returns only bounded generation/time metadata.

Within one synchronous copy attempt, source, copied state and immediate
pre-publication source validation may retain their exact mode/field/value
privacy-success cache. It never caches rejection or replaces other checks.
The closed private copy receives an independent full inspection. Retained artifact reuse now uses
the content-bound record contract below, rather than the source's privacy cache.
No artifact-local successes flow to another artifact or later mutable source.

Each publication phase performs common structural/binding/maintenance admission
and its exact-schema full row audit once in the same SQLite read transaction;
the common admission does not repeat the exact-schema history traversal. The
private copy starts its own read transaction after copying. Recovery-content
classification uses the same composition within its own snapshot, including
when canonical artifact inspection invokes it. Initial structural/set
inspection, reconciliation, source, copy and fresh pre-publication observations
remain independent; no file validity is inferred from size, mtime, a successful
earlier phase, or another closed snapshot alone. A matching immutable record is
the explicit content-bound exception; all uncached/current checks remain.

`_copy` scopes its initial source with an `ExitStack`, closed immediately after
the backup API returns and also on every earlier exception. Destination
validation and file preparation retain no initial live-source reader. The fresh
source remains open through atomic replacement under the existing artifact
lock. Setup and pre-migration publication use this primitive; the asynchronous
routine uses the separate capture/publication path below.
The two adjacent backup-API producers, private-stage snapshot copy and managed
generation restore, release their initial source at the same copy boundary;
their existing identity, recovery-observation, validation and no-clobber guards
remain independent and unchanged. Evidence/Viewer capture already closes its
source before rendering and uses its own fresh publication guard; it does not
use this copy primitive or inherit a backup privacy cache.

Managed names are exactly:

```text
taskgov-backup-v1_<YYYYMMDDTHHMMSSZ>_<32hex>_r<1-20>.sqlite
```

The generation ID is `tg_backup_<same 32hex>`. Other names and nonregular,
linked, invalid, or foreign files are never managed or pruned.

Schema v11 `managed_backup_generations` is the authoritative retention set.
Every setup/routine backup under the same zero-wait artifact lock reconciles
at most the bounded file/row crash residue: import one valid file-only
generation, remove an unusable row target without following an untrusted path,
and complete file-before-row pruning using applied retention. Publication
writes the derived validation record and file, then inserts its row and updates pointer/outcome/applied
retention in one short transaction, then prunes file before row. Failure leaves
the work due. Crash residue is bounded to the prior valid set plus one
in-flight generation.

Setup migration always makes the pre-migration managed backup before changing
schema and binds metadata/pointer before pruning. Rollback therefore means
restoring a matching package, database, and artifact set together; no reverse
migration exists.
Schema 29 publication and recovery delegate to the same exact continuation
storage validator, including frozen graphs, original bindings, note privacy and
ownership. Recovery may classify the existing bounded verification rejection
only after every other relation/privacy check; it cannot fall back to an older
generation around a corrupt note or continuation graph. Source-28 migration
backup remains validated under its exact predecessor contract before the
explicit 29 migration.

#### Immutable Backup Inspector

`backup_validation.py` owns the closed record codec, bounded physical record
inventory, private-copy certification, read-only artifact inspection and
derived-record publication/cleanup. `state_resolver.py` owns the fixed
`backups/validation` path and typed database/backup observations. Backup and
asynchronous capture retain effect ownership; metadata repositories remain
the only generation/pointer writers. The record is not a SQLite table and
does not authorize a new public path or option.

The [record contract](setup-state-specification.md#immutable-backup-validation-records)
fixes format/rule versions, project/generation/schema/hash bindings, mode/scope,
classification, size bounds and crash order. Current complete success is the
only reusable classification. Changing a certified validation rule requires
advancing the rule version; version/scope misses run the actual needed audit
without read-path enrollment. A present damaged/mismatched record fails closed.

The inspector pins the actual SQLite read snapshot before taking one no-follow
content fingerprint and checking its record. Fresh schema/project/binding and
repository metadata and that fingerprint belong to the same retained read lock.
A certified record skips only schema/history/object/integrity/domain/privacy
audit; fallback runs the audit in that snapshot. Same-file identity is required
throughout. The hash descriptor is kept in an outer resource scope until after
the SQLite reader closes, on success and every error path, avoiding POSIX
process-lock cancellation. The record is reread before admitting the observation.
It returns the digest with the typed observation so classification and selection
do not hash or audit the same bytes again within that observation. SQLite copy
output is separately inspected and hashed; it cannot inherit its source digest.
No raw-file descriptor is closed against an actively held SQLite read lock on
that same file. Private-stage recovery copies may consume the exact pinned
artifact snapshot; source internal audits are not repeated. The copied output
receives its independent validation after the source closes, and the final
source continuity check uses the same proof-aware inspector. The separate closed
private copy may be fingerprinted while a live-source publication guard is held.
Both synchronous and asynchronous publication repeat this closed-copy check
after fresh source/control admission, immediately before the file replacement.
They also check the newly published record's exact bytes, binding and file
identity; asynchronous publication checks it again after file and metadata
publication. Before destructive retention, synchronous pruning, synchronous
v11+ generation reconciliation and asynchronous pruning refresh the surviving
artifacts' content and eligibility outside metadata writers. Discovery retains
the complete typed observation, including content digest and project/binding,
even for recordless artifacts. The effect-boundary refresh must equal that
observation and physical identity; a different valid database is still drift.
File-only generation imports use this same continuity check even when they
will not prune; v10 pointer recovery has the same boundary.
Before reconciliation imports or retention, the shared resolver validates the
already inspected candidate observations against a fresh bounded read of the
primary's identity, lineage and generation repository. Candidate prefix,
pointer and cross-generation metadata checks are the same as recovery's;
healthy-primary Setup remains shallow. The surviving set must itself retain
an eligible candidate at its structural head's binding. These checks prevent
discarding the last normal generation without repeating internal content audits.
No heavy content audit or hash runs under a metadata writer.

Setup recovery planning and backup-only old-layout cutover read policy through
the same proof-aware artifact snapshot. They compare the selected observation's
digest, physical identity and recovery relationships, then read the bound
maintenance policy in that pinned snapshot without another internal content
audit. Missing or obsolete records still require the inspector's real audit.
Mutable primary, staged/normalized copy and post-restore Setup inspection keep
their independent full validation; this planning projection is not a reusable
mutable-database admission result.

Record publication is under existing artifact ownership, through one durable
`.pending.json` then atomic replacement to the absent generation record. It
precedes backup publication; interruption cannot leave a new artifact appearing
to be an old recordless generation. A bounded valid orphan grants no business
authority and is removed only by a subsequent authorized backup. Generation
uniqueness is checked before any reconciliation effect, including when one
duplicate's record fails to match. Existing pruning removes the file before
its metadata row and removes its validated derived record; crash residue is
bounded. No diagnosis/preview writes records or performs cleanup.

### Asynchronous Backup Worker

`async_backup_opportunity.py` owns bounded file publication and service ownership;
`async_backup_service.py` owns the existing MCP service's opportunity observer.
`async_backup.py` owns service-invoked bounded startup and the single-attempt child entry;
`async_backup_state.py` owns its closed runtime record and OS-lock observation.
`async_backup_repository.py` owns the dedicated control read and short metadata
writers. `async_backup_capture.py` owns private-copy capture, validation,
publication, reconciliation and retention. Shared synchronous Setup/recovery
helpers retain their own admission and resource lifetimes.

The coordinator first uses a narrow repository eligibility read of the current
project/binding and maintenance policy. This is not structural admission or a
Task-history audit, and it supplies no proof to backup publication. Only opted-in
due/recovery work reaches the file publisher, which opens no database. An absent
or incompatible service returns deferred without a process launch, synchronous
fallback, service start, configuration write or extra LLM operation. The CLI
waits only for this bounded file publication, not service consumption or worker
readiness. `requested` proves publication alone.

The runtime directory also contains fixed `service.lock`, `service.json`,
`.service-next.json`, `opportunity.lock`, `opportunity.json` and
`.opportunity-next.json`. Service lifetime ownership uses the OS lock; the short
opportunity lock serializes file I/O. Closed records are at most 512 bytes. An
opportunity binds version, request and service-owner identities, project,
binding path hash/generation, core schema and the version-one compatibility ABI.
The service record retains the same basis without a request identity. PID,
saved presence or a lock file alone does not prove current service ownership.
Fixed publication temporaries are bounded control residue; unknown objects are
never adopted or deleted. These files contain no Task content or raw paths and
are not recovery artifacts or completion evidence.

After fresh core readmission under the exclusive service lease has closed its
transaction, activation takes the short opportunity lock to replace service
presence using that exact target. Existing service
and opportunity records must be well-formed, physical, single-link and belong
to that durable project; differing binding hash/generation is permitted only
as inactive residue. Activation baselines the old request without rewriting
or dispatching it. No generation ordering is inferred from these non-authority
files, including after restore. The publisher still requires exact current
service binding, owner and compatibility before replacing a same-project old
opportunity with a fresh request. Observation, launch control and publication
guards retain exact binding checks. Foreign, malformed or unsafe records remain
untouched and fail closed; this does not rewrite core binding, `state.json` or
backup artifacts, or broaden Setup.

Only the actual MCP service entry starts the observer. Constructor, discovery
and tool calls do not start it. Startup admits current schema, binding and
maintenance without a complete Task audit or global resolver scan. Immediately
after acquiring service ownership, including after standby, it reads that core
basis once more and requires enabled policy and exact equality with the startup
target. The transaction closes before presence activation. Failed readmission
or presence activation after lease acquisition ends this observer until service
restart; neither becomes a ten-second DB retry. Standby retries only OS-lease
contention. After successful admission and activation it baselines
any existing request. Every ten seconds
it reads the single fixed opportunity; absent/unchanged input opens no database
and hashes no package contents. A new request for the current owner and basis
is consumed at most once, including failed or unknown startup. There is one
current nonce, no queue, replay ledger or guaranteed delivery. Startup and
ownership takeover do not replay older requests; a later eligible changed
mutation supplies recovery. Presence checks use ABI and schema compatibility;
full loaded/deployed runtime identity is checked only after fresh due admission
and before actual worker startup effects.

The fixed resolver supplies `.taskgov/current/backups/runtime/`. It contains
the closed runtime control files `state.json`, `.state-next.json`, `state.lock`,
`launch.lock`, `worker.lock` and the optional 32-byte `.launch-uncertain` attempt marker; the JSON
record is at most 8 KiB. It retains one attempt, not an unbounded queue or
history. Atomic replacement follows physical-directory and regular-file checks.
Every runtime-state reader and writer uses the same short `state.lock` while
opening and closing the state file. Writers publish the fixed temporary with
ordinary atomic replacement only after readers have closed their handles;
there is no Windows-specific multi-rename publication or random temporary
history. Only writers may create the lock. Once created, the lock is never
replaced or removed. Its physical, single-link and file-identity checks apply
before and after acquisition. The reader retains those checks for the state
file and its parent as well; changed or unavailable observations remain unknown.
Internal access waits at most 0.5 seconds to acquire this lock. Startup reads
instead share the service launcher's remaining absolute handshake deadline. Diagnosis
uses only an existing lock with zero wait and never creates or repairs one.
No database work, process wait or backup operation runs inside the state-file
critical section. The fixed `.state-next.json` is the only publication residue
the writer owns; unknown artifacts from older development attempts are neither
adopted nor deleted. This does not alter the common state-file reader.
No task content, command body, exception text, credential or raw output is
stored. Runtime control is neither a recovery source nor completion evidence;
the core managed-generation rows remain authoritative for successful backups.
The worker's one private SQLite snapshot `snapshot.sqlite`, its optional
`snapshot.sqlite-journal` rollback sidecar, and versioned `snapshot-owner.json`
also live in this runtime directory. The managed-backup lock and validated
owner marker govern cleanup of those exact physical single-link scratch
objects. The marker is at most 512 bytes and binds its version, fixed purpose,
project identity and attempt identity. Recovery checks this scratch before
reconciliation and the due decision, including when no new copy is needed.
Restart never deletes an unknown path or accumulates a new randomly named
scratch file for every attempt. Private copied data is not part of the bounded
JSON control record.

The zero-wait launch lock serializes launch decisions. The separate zero-wait
worker lock owns one running attempt, and the existing managed-backup lock
serializes backup publication with Setup. A child receives the attempt identity
and may act only while it still matches the bounded record. An old process
cannot overwrite a later attempt merely because its PID still exists.
The service waits at most two seconds for startup readiness after process
creation, never for copying, verification or publication. Startup failure
cleanup adds at most 0.75 seconds of child-exit waits. These bound the handshake
and exit waits, not the duration of process creation, filesystem operations or
database admission. The child performs no backup effect until the service sends
its one-shot start signal; EOF before that signal exits without starting backup
work. Startup failure cleanup is limited to the child the service just created.
If that cleanup cannot confirm the child exited, the service records the
separate launch-uncertain marker without overwriting the child's state. No
replacement child may start until a matching terminal observation and released
ownership establish that the uncertain attempt ended. Time or PID reuse does
not clear that barrier.
The launcher creates this independent marker under launch ownership without
acquiring `state.lock`; an unconfirmed child holding the state lock must not
prevent that barrier from being saved. The child never changes the marker.
The worker retains no polling loop after startup and creates no timer. The
service's opportunity observer remains independent of this single attempt.

The service-owned child outlives the requesting CLI and performs one attempt
while holding the worker and managed-backup locks. Repeated normal
updates observe the existing ownership and create no additional child or queued
request. Failed launch and process termination leave retryable state for a
later eligible mutation. A saved running phase without current ownership is
interrupted, not proof of a running process. Invalid or uncertain observation
is unknown; read-only diagnosis never creates locks, rewrites records, retries
or starts a child.
Review-wait policy, reviewer completion, cancellation and timers do not control
the backup observer or worker. Backup completion leaves the MCP service alive.
Service/host shutdown may interrupt an attempt; the last normal generation and
next-mutation recovery remain intact. The observer is not a backup schedule or
a new host timer.

The routine repository validates its consumed live basis in short snapshots:
regular-file instance, rollback journal, current schema/migration identity,
project/binding and maintenance/generation rows. It captures a consistent
SQLite copy and closes the live reader immediately. Complete structural,
integrity, foreign-key, domain-relationship and privacy validation uses the
private copy only. Heavy retained-artifact classification likewise keeps no
live reader open. A fresh publication guard and metadata writer compare the
required file/binding/schema and inventory basis without repeating a complete
history scan. The database owner's
[admission exception](database-specification.md#asynchronous-backup-admission)
does not authorize a public bypass or a cross-transaction validity cache.

The routine reconciler checks generation-ID uniqueness across recognized physical
artifacts before applying a metadata delta or pruning; its ID index must not
silently collapse duplicates. Ambiguous duplicates fail without pruning files
or discarding retained success rows;
failure-outcome recording still follows its separate guarded writer contract.

Capture establishes `captured_at`; successful file publication and metadata
confirmation establish `published_at` and the generation identity. Updates
committed after capture are permitted and belong to a later due attempt.
There is no loop chasing the newest Task mutation and no promise of one backup
per Task completion. File publication, core metadata confirmation, and pruning
retain the existing ordering and bounded crash-reconciliation rules. A worker
failure preserves the latest confirmed normal generation and cannot turn the
already successful command into an error.

### Post-Commit Coordinator

The [incremental Viewer protocol](viewer-design.md#incremental-cache-and-publication)
owns Viewer capture/guard/outcome admission.
The [tracking foundation](viewer-design.md#incremental-tracking-storage)
requires an epoch reset in formal private restore/publication before canonical
publication, preserving the exact source comparison before derived-state
normalization. Evidence and backup remain outside the Viewer replacement.

Eligibility snapshots use structural admission and validated policy/generation
metadata, without whole-project Task-history validation. Evidence repeats
eligibility under its artifact lock and validates only unpublished Bundle
closure under the [Evidence publication protocol](evidence-design.md#provenance-evidence-ledger-and-bundle-structure).
Explicit Setup repair and physical inspection retain complete-source validation.
Viewer repeats eligibility under its lock, reads changed Task projections in one
snapshot and rebuilds all display Tasks when cache reuse is invalid. Its
[guards and result writers](viewer-design.md#incremental-cache-and-publication)
are fresh structural transactions without global history audits. Explicit Setup
also validates cache bytes and repairs missing, corrupt or unmatched cache even
when HTML remains valid. The retained synchronous backup primitive's initial policy
and generation inventory read alone is local; its cheap crash-residue comparison
does not certify artifact contents. An explicitly admitted backup attempt retains
the global locked resolver/repository checks and complete source, copied-file
and pre-publication validation. Publication guards and success/generation
writers remain global. Failed/deferred outcome-only writers use structural
admission before and under their short writer, then conditionally update against
the observed backup success tuple, outcome tuple and ordered generation metadata.
The inventory is re-read and compared under that same writer, so prune-only
recovery, equal-count replacement and metadata-only repairs invalidate stale
outcomes without artifact I/O or a Task-history audit. Viewer-only changes do not
invalidate that basis. A lock-entry failure uses the pre-lock observation;
an in-lock failure observes its own post-publication/reconciliation state before
releasing the artifact lock, so a later prune failure is preserved. An unreadable
basis or failed outcome write is bounded and cannot replace the primary result.
The repository observation pins regular-file device/inode identity across its
read; the outcome writer checks it before opening and again under its writer.
Replacement makes that outcome stale even when restored metadata has identical
values. Size/mtime are not compared and no content-validation proof is cached.
Setup/doctor/recovery defaults remain global; there is no new proof cache.
The asynchronous routine has the separate live-control and private-copy
boundary above; it does not invoke this synchronous path from the caller.

Business services retain internal `MutationOutcome(changed, viewer_relevant)`. After commit and connection close, every changed mutation may retry due Evidence projection; the coordinator runs:

1. Evidence projection when due;
2. Viewer refresh when relevant;
3. one bounded asynchronous-backup opportunity publication for the existing
   compatible MCP service.

The first two remain independent same-process bounded work, never a thread,
daemon, timer, queue, scheduler, service, sleep, or retry loop. Each uses its own zero-wait
one-byte OS lock; lock-file existence is not ownership. Read, error, replay,
no-op, configuration-only setup, doctor, Effort, and maintenance metadata
writes do not invoke it. Runner success and fallback retain the one original
target-set coordinator pass; a post-T1 Runner error does not invoke maintenance
and leaves the already-advanced due state for a later normal opportunity or
setup repair. Runner-internal writes never invoke a second pass.

No ordinary mutation calls the retained synchronous `run_routine_backup`,
copy, reconciliation or pruning path. Task add/edit/complete/checkpoint,
handoff record/withdraw, review target/receipt/finding add/resolve and
Verification Receipt add share the asynchronous opportunity publication after a
real committed change. Handoffs and Verification Receipts remain
Viewer-ineligible for their existing reasons. The established interval,
retention, success metadata and generations are preserved. Doctor observes
their stored state plus the separately timed worker observation; it does not
schedule, recover or launch work.

Only completion-cycle insertion increments Evidence source generation in its business transaction; a later changed mutation may retry an already-due projection without a third outcome flag. Viewer-relevant mutations increment source generation through `task_events` in the same transaction. Schema 34 also marks event-free Verification Receipt and continuation changes through the locked tracking repository; neither path adds a post-commit notification write. Viewer refresh renders and rechecks once at most; setup uses direct Evidence/Viewer stages and does not re-enter the coordinator.

Only fixed warnings are added after a successful primary command:

```text
evidence_projection_deferred | evidence_projection_failed
viewer_refresh_deferred | viewer_refresh_failed
backup_request_deferred | backup_request_failed
```

Messages state that the primary result is unchanged and include no path, exception, hash, raw output, retry, stop, or model choice. Doctor observes the latest outcome without starting work.
