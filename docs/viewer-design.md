# Static Viewer Implementation Design

This document owns the current Viewer implementation structure delegated by the
[implementation design](design.md#static-viewer), for the behavior in the
[Viewer specification](viewer-specification.md).
The shared [fixed state resolver](setup-state-design.md#fixed-state-resolver),
[stored-Task and batch validation](task-operation-design.md#shared-stored-task-rowbatch-validator),
[Contract pointer validation](task-operation-design.md#stored-contract-pointer-relationship-boundary),
[completion history](review-completion-design.md#completion-cycle-history), and
[post-commit coordinator](setup-state-design.md#post-commit-coordinator) remain at their
existing owners.

## Snapshot And Publication

Current schema-34 maintenance uses the
[incremental protocol](#incremental-cache-and-publication). This section
documents the retained full-capture reader and common v4 rendering/path
boundaries used by explicit inspection and compatible older sources.

`viewer_metadata_repository.py` owns the maintenance read/seed and publication
and attempt-outcome records. Incremental effects use the fresh structural
guard/writer protocol below. The full snapshot reader retains complete source
admission; its validation does not certify a later publication transaction.

The Viewer is a replaceable projection, never authority:

```text
committed Viewer-relevant event
  -> source generation
  -> post-commit coordinator
  -> zero-wait Viewer lock
  -> one query-only selected capture and checked display cache
  -> snapshot v4 + captured generation
  -> base64 UTF-8 JSON in bundled template
  -> atomic task-viewer.html replacement
  -> short matching cache/HTML generation update
```

Setup invokes the same canonical renderer directly. There is no public Viewer
command, output choice, browser launch, server, or browser-to-SQLite path.

`build_viewer_snapshot` retains composed complete admission for explicit full
inspection and compatibility. Routine `_capture` uses the selected repository
and independently checked cache under the protocol below. Metadata eligibility
never supplies a reusable source proof or relaxes generation/opt-out checks.

For a current source, `storage._validate_snapshot_database_state` composes the
following responsibilities within that single query-only call chain:

| Responsibility | Validation owner |
|---|---|
| Exact schema, complete migration history, project binding and maintenance metadata | Current structural admission; Viewer compatibility/error checks remain |
| All Task/Contract rows, authority, References, Receipt/Finding/provenance and Bundles | Complete Evidence Ledger validation, returning the existing private Task batch proof |
| Global review-session bindings, including orphan/foreign relations | The Ledger's complete Receipt validator; no second admission scan |
| Runner graph and collected-answer/source/dispatch and judgment storage | The existing current Runner and collected-review admission helpers |
| All ownership/execution history and retained note bodies/parents/privacy | Their original global validators, once per capture |

Only a successful fixed composition establishes these facts; no public flag,
caller-supplied certificate or persistent cache is accepted. Selected binding
reads used by provenance, history and projection keep their individual checks.
The same current-source composition serves direct snapshot consumers, including
Setup's physical Viewer inspection. Older supported sources retain their own
source-version admission; they cannot borrow current-source results. No result
crosses the capture's transaction into a guard, another capture or outcome writer.

The repository selects complete rows for all project Tasks in list order and
validates them as one source-schema-aware stored-Task/Contract batch before any
Task, review, or history projection. Exact v18-v34 snapshot validation first
validates the complete Task batch as part of the full Evidence Ledger, then the
same-transaction list-order query consumes the
[private batch proof](task-operation-design.md#shared-stored-task-rowbatch-validator) instead of
repeating scalar, privacy, and Contract validation. Sources v5-v17 and the
ordinary Viewer repository entry still validate the selected batch directly.
Validation failure occurs before rendering or replacement and preserves the
last-good HTML. It then selects at most 10 newest events per Task by time/rowid,
review evidence through the shared gate read model, and bounded completion
history. Snapshot v4 contains snapshot/source schema versions, generated time,
project ID/display only, counts, and explicit Task/event/evidence/history allow-
lists. It excludes repository/database paths, tool events, handoffs,
checkpoints, maintenance state, internal generation, event-cycle links,
environment, and raw review material.

Within that transaction, `reviews.py` enumerates the Finding inventory once
for the complete validated Task batch, ordered by Task list position and
Finding rowid. The selection retains TEXT/BLOB Task aliases and the global
orphan branch, including exact parent identity and alias-candidate counts.
A private cursor view passes each Task's complete stream through the existing
owner, domain, privacy, Reference/provenance validation and gate aggregation
in bounded chunks. It retains only one lookahead row, not a project-wide
Finding cache or a validation proof. Orphans enter the first Task's validation
and fail closed; no recent-ten limit filters validation. The enclosing batch
closes the shared cursor on success or failure. Receipt and Reference streams,
all-generation counts, gates, and timestamp/rowid recent ordering are unchanged.
An empty Task batch opens no inventory. Ordinary repository callers without
an existing transaction retain scalar reads rather than sharing observations
across SQLite snapshots. This adds no transaction, schema, public argument,
maintenance trigger, or publication permission.

Schema 28 additionally validates every retained Task note and parent binding
before projection, while the event allow-list remains summary-only. Full note
bodies never enter Viewer snapshot JSON or generated HTML.

Source schemas v5-v14 synthesize empty/incomplete completion history;
v15-v34 use stored cycles, reading completion histories in batches of at most
500 Task IDs. Sources v17-v34 validate version-1 cycle Receipt links; v19-v34
also validate and discard Bundle linkage, and v20-v34 validate the source-
appropriate closed verification basis and Runner graph. Sources v21-v34 validate
the complete tagged union before discarding every Runner field. Every snapshot
reports its actual source schema and selects all
project Tasks; 500 Tasks is the accepted performance fixture rather than a
selection cap. The rendered artifact is capped at 64 MiB.

Sources 30, 31, 32, 33 and 34 go through their exact source-schema storage admission, including
[collected-answer persistence](database-specification.md#collected-answer-persistence)
and [versioned continuation graph validation](evidence-design.md#continuation-graph-validation).
The batch proof remains transaction-local. Bundle 4/graph 2 and v2 provenance
are validated before projection and then discarded like their predecessor
formats; preserved sealed formats retain their exact version routes. The shared
gate supplies effective unable behavior without a parallel Viewer predicate.
Snapshot v4, its allow-lists, byte cap and publication sequence do not expand to
include original answers, transport paths, collection state or an Evidence UI.

Snapshot JSON is deterministic UTF-8 and base64-encoded before insertion.
The template has exactly one snapshot placeholder and one decimal refresh
interval placeholder. Stored data reaches the DOM only through `textContent`
or text nodes, never HTML/eval/URL/style/event sinks.

The canonical output and lock remain below fixed state. Resolution rejects
reparse parents, containment changes, DB aliases, and linked/nonregular
destinations. Rendering writes a unique sibling temporary, flushes/closes it,
and `os.replace`s only after complete success; failure retains last-good HTML.

Schema v13 `viewer_maintenance_state` holds nonnegative source generation,
nullable rendered generation not above source, and fixed
`succeeded|deferred|failed` outcome/time. New/migrated state starts source 0
and rendered null. Under the lock, publication captures generation and rows in
one snapshot, closes SQLite, renders/replaces, then conditionally records the
captured generation without lowering a newer value. One recheck permits one
follow-up; later churn remains due.

<a id="conditional-incremental-design"></a>

## Incremental Design

This is the implementation owner of the current
[incremental behavior](viewer-specification.md#conditional-incremental-projection).
It uses a latest-change row per Task, a project invalidation boundary and one
sanitized display file. It is not a general change-log engine. The public
snapshot remains v4. All repository calls below are internal, use the fixed
resolver, and never infer permission from cached state.

### Incremental Change Inventory

The following inventory maps current writers/read dependencies to the required
tracking boundary. The storage unit must cover every row, including paths that
do not emit a Task event. An event is a notification, not a timestamp heuristic.

| Change / current owner | Affected display set and notification |
|---|---|
| Task add/edit, status, priority/lane/order, verification declaration, target, completion projection and reopen (`tasks.py`, `reviews.py`) | Own Task. The existing `task_events` insertion advances the source and records that Task in the same transaction. Reordering/counts are recomputed from cached display rows, not by marking unrelated Tasks. |
| Contract create/revise, checkpoint and retained-note event (`contracts.py`, `checkpoints.py`, `tasks.py`) | Own Task via the event, even when its main row timestamp is unchanged. Contract/note/checkpoint content stays excluded. |
| Review Receipt, Finding add/resolve/reassessment (`reviews.py`) | Receipt's own Task via its event; parent and foreign-identity validation remains required. |
| Verification Receipt and its Reference (`verification_receipts.py`, `verification_receipt_repository.py`) | Own Task through an explicit locked repository mark, because this path has no Task event. Conservative reprojecting is permitted even where v4 currently discards the result; no synthetic event is added. |
| Continuation prepare/retire/register, successor judgment, explicit reassessment link (`review_continuation_repository.py`) | Bound Task through the same locked mark when no encompassing Task event covers the mutation. A valid non-PASS head also changes effective review state. |
| Collected-review registration, source/provenance/criterion links (`review_answer_registration.py`, `review_answer_repository.py`) | Every registered Task through the enclosing registration transaction; no reliance on host notification or successful Hook delivery. Unregistered transport/capture/control writes alone are not display state. |
| Completion cycle, Bundle, event-cycle link and reopen compatibility cycle (`tasks.py`, `storage.py`) | Own Task through the completion/reopen transaction's event, after all coupled writes; frozen older cycles remain unchanged. |
| Authority/criterion/manifest/Reference, Runner verification and session binding records consumed by display readers | Their supported publication is part of the owning target/Receipt/registration/completion transaction above. Pure intent/control/observation/ownership activity is excluded. A new writer publishing a consumed relation must mark its owner before commit; it cannot depend on `updated_at`. |
| Supported update affecting several Tasks | Mark the complete distinct affected ID set in the same transaction. Resolve relationship owners by indexed keys; no semantic sweep of all Tasks to discover them. If the set cannot be bounded from owned relations, invalidate the whole display. |
| Project display/binding changes (`project_binding_repository.py`), migration, formal restore or fixed-state publication (`setup.py`, `backup.py`) | Whole-project invalidation with the identity rules below; binding/display are not Task events. |
| Template/projection format change; explicit Viewer repair or reload-policy selection | Force the appropriate rebuild/render through Setup or the version check on a due opportunity. No config watcher or automatic settings write. |
| Handoff record/withdraw, Effort, usage collection, backup/Evidence metadata, wait timers, unregistered review transport, read/error/replay/no-op | No display invalidation or new coordinator call. They are not sources of v4 display fields. Existing due-Evidence/backup opportunities remain unchanged. |

The existing event trigger is extended, not accompanied by a second event
counter. A non-event writer calls the same repository operation inside its
already-owned transaction. Multiple notifications in a transaction may advance
the generation more than once; only monotonicity and complete coverage matter.
Exact replay/no-op does not mark. Overflow or missing tracking parents aborts
the business transaction. No write follows commit just to deliver notification.
The storage tests must exercise each mutation family above through its real
repository/service, including multi-Task and rollback cases. A new writer or
new displayed field must extend this inventory and its coverage test.

### Incremental Tracking Storage

The storage-foundation migration (v34 from v33) is owned by a dedicated
`schema_viewer_projection.py`; `viewer_projection_repository.py` owns its reads
and writes. It adds two relations without changing business IDs or saved
evidence. Exact DDL/fingerprints, typed row validation, preservation and migration
history follow the [database migration owner](database-design.md#migration-sequence).

* `viewer_task_revisions`: `(project_id, task_id)` primary key with Task/project
  parent checks, and `source_generation` as a strict nonnegative SQLite INTEGER.
  An index `(project_id, source_generation, task_id)` supports the changed-ID
  range. One upsert retains the greatest revision for each Task; no event log
  is copied and no consumed row is deleted. Existing Tasks are seeded at the
  migration's current source generation while invalidation requires a rebuild.
* `viewer_projection_state`: one project parent row, `database_epoch` as 32
  lowercase hexadecimal characters from a fresh random 128-bit value,
  `invalidated_generation`, and nullable coherent cache/publication tuples.
  The cache tuple is `(cache_generation, cache_format_version,
  projection_version, cache_sha256, cache_bytes)`; the HTML tuple is
  `(html_generation, html_sha256, html_bytes)`. Hashes are lowercase SHA-256
  hex; byte counts are strict positive INTEGERs up to 64 MiB. Each tuple is
  wholly null or wholly present. Versions begin at integer 1. All generations
  are strict INTEGERs in `0..source_generation`, never Boolean/coerced text.
  `html_generation` mirrors existing `rendered_generation` on the new route,
  atomically in the same metadata transaction; null HTML tuple means not yet
  published for the accepted cache by that route, not corrupt legacy HTML.
  Accepting a cache clears the entire HTML tuple and `rendered_generation`
  atomically, even if C, its digest or its version equal their previous values.
  Successful HTML publication restores them only against that exact accepted
  cache tuple. This permits same-generation rebuilds without treating an old
  HTML publication as proof that the new publication finished.

Let S be the current existing Viewer source generation, I the invalidation
generation, C the accepted cache generation, and R the accepted HTML generation.
Changed IDs are `project_id = P AND source_generation > C AND
source_generation <= S`, ordered by generation/ID using the index. C below I
requires rebuilding all display Tasks. A missing cache tuple also requires a
rebuild. Queries return IDs/revisions only; capture of their source rows occurs
in the same read snapshot as S/I. A missing Task for a recorded ID fails closed:
hard Task deletion is not a supported writer, and this design introduces none.
No delta reader scans all events or all Tasks as a correctness shortcut.

Every mark advances `viewer_maintenance_state.source_generation` and upserts
the affected rows in the same transaction. Global invalidation advances S and
sets I to it; it does not update every Task revision. Generation overflow is a
closed failure. Cache application records only the captured C; it neither
resets S/I nor deletes revisions. Thus a newer revision committed while a
capture is being rendered remains selected by the next range query. Normally
R <= C <= S when the tuples are present; a failed/unacknowledged replacement can leave file and metadata
generations different and follows the recovery matrix below.

Initialization/migration creates a new epoch and null cache/HTML tuples, without
rewriting the existing last-good HTML or business history. Formal managed-backup
restore and legacy/fixed-state publication rotate the epoch and null these
tuples in the private candidate before its canonical publication. Compare the
copy to the selected recovery source before this narrowly allowed derived-state
normalization, then revalidate the normalized candidate; never weaken backup
selection or alter the source backup. The normalized recovery observation must
explicitly allow only this derived-state delta in addition to its existing
repository normalization. Reentry after canonical publication sees the rotated
epoch; it does not rotate repeatedly or replay restore. Restoring an old schema
gets a fresh epoch on explicit migration. Confirmed rebind invalidates in the
same binding writer; fresh capture must match the new binding generation/hash.
These changes are part of the approved Setup operation, never ordinary implicit
repair. Physical DB replacement also invalidates a saved cache by file identity;
in-place unsupported edits with identical epoch are outside the guarantee.

### Incremental Cache And Publication

`viewer_projection_cache.py` owns the bounded file codec and physical target
checks, not SQL admission. The shared resolver supplies its canonical path
beside the resolved Viewer HTML under `<fixed-root>/viewer`; `DatabaseTarget`
carries it, and injected test targets use that same name beneath their database
parent. The integration unit extends the exact managed-artifact inventory for
this one cache and its sibling temporary grammar. Cache, HTML and temporaries
share the existing canonical Viewer lock; no `.taskgov/viewer` sibling or second
artifact root is introduced. The cache is strict UTF-8 JSON with no duplicate,
unknown or missing keys. Its exact top-level keys are `cache_format_version`,
`projection_version`, `project_id`, `binding_generation`, `binding_path_hash`,
`database_epoch`, `database_file_identity`, `source_schema_version`,
`applied_generation`, and `tasks`. Identity contains only integer `device` and
`inode` from the regular opened database file (Windows file identity through
the same physical-file helper), not a path or mtime. Binding hash uses the
existing canonical-path hash. `tasks` is the complete deterministic ordered v4
Task array including its current allow-listed events/review/history. Counts
and project display name are assembled afresh from this array and current
project metadata; generation/identity/header fields never enter the public v4
snapshot. Exact nested keys, types, privacy and field capacities are validated
from the display schema, without database joins or evidence parsing. Duplicate
Task IDs and an incorrect sort order fail cache validation.

The whole file is bounded at 64 MiB before allocation/parse, and its canonical
bytes and SHA-256 are compared to the DB cache tuple. Header identity, versions,
generation and source schema must equal the admitted metadata/physical file.
Noncanonical bytes are invalid. A cache digest proves only matching generated
bytes, not freshness of arbitrary source files or protection against an attacker
rewriting both DB and cache. No stamp, previous private transaction proof or
caller-supplied validation flag substitutes for these checks.

`viewer_maintenance.py` retains the single zero-wait Viewer artifact lock for
the following sequence, at most twice per opportunity:

1. Perform cheap structural/maintenance eligibility and fixed-file presence
   checks. Disabled/current exits read no cache body or historical evidence.
   Repeat eligibility after locking. Unsafe paths fail; missing files are due.
   On the new route, current additionally requires complete compatible cache
   and HTML tuples, C = R = S, C >= I, and no failed/deferred outcome or force
   request. The legacy rendered counter alone is insufficient. Epoch-reset
   null tuples therefore remain due even if that counter still equals S.
   For explicit force repair, a fresh short structural writer under this lock
   first clears both cache/HTML tuples and `rendered_generation`, preserving
   the files, business revisions, S/I and last-success time. Force repair thus
   performs a full display rebuild and remains due after process termination,
   even before cache replacement. An unknown outcome of this metadata write
   is not replayed; a later opportunity reads its actual committed state.
   Perform this force invalidation once at attempt entry, never again during
   the one permitted follow-up capture.
   When due, load/hash/validate any candidate cache under the artifact lock,
   outside SQLite, before opening the capture snapshot below. Invalid candidate
   bytes request a rebuild; they never authorize writes to an unsafe path.
2. Pin regular DB identity before opening and again within one query-only
   snapshot. Admit schema/history/project/binding/policy and tracking metadata;
   read S/I/cache tuple there. A usable cache plus C >= I selects only changed
   IDs. Otherwise select all Tasks for one full display rebuild. Obtain and
   validate all affected display rows and their needed relations in this same
   snapshot. Close it before serialization, hashing or file publication.
3. Reuse current stored-Task/Contract, own-review and completion-history
   readers, adding selected-ID forms where needed. In particular the current
   `_read_viewer_review_evidence` global orphan/Finding stream is not the delta
   reader. Selected reads must use indexes, reject foreign related evidence,
   and retain complete relevant own history even beyond the displayed ten.
   The existing batch completion-history reader assumes upstream global Bundle
   admission; the new selected path must instead validate the selected Tasks'
   Bundle/history relations explicitly, using the single-Task boundary or its
   equivalent selected batch. It must not silently reuse the old assumption.
   `viewer_task_projection_repository.py` owns that selected read. Its Finding
   query enumerates the closed status/severity domains to seek the existing
   compound index by Receipt; same-snapshot schema and physical CHECK admission
   prevents those predicates from hiding invalid domain values. All own
   judgment rows are checked even when no continuation is present. Own cycles
   are validated in bounded chunks before retaining their newest display window.
   Event ownership is checked in both directions: all selected-Task events and
   the identities of their referenced cycles, plus all incoming links to each
   selected cycle. These indexed reads include links older than the displayed
   ten events and do not validate another Task's cycle evidence.
   Each chunk validates historical Receipt/Bundle links without interpreting its
   last cycle as current. After the complete ordered stream, only the actual
   latest cycle is compared with a done Task's current verification requirement.
   Reopened Tasks retain historical validation without that current comparison.
   `viewer_display_validation.py` validates generated bytes without SQL.
   Text checks retain each source field's privacy policy, including the distinct
   title and manual-diagnostic quotation rules and stricter evidence fields.
   It shares exact-field/text privacy and timestamp predicates within a publication
   attempt, including cache read and re-encoding. Successful pure privacy
   checks additionally use a process-local LRU of at most 16,384 strings, each
   at most 1,000 characters, keyed by source field, exact text and predicate callable.
   The attempt-local privacy memo uses the same key, so approval in one field
   cannot certify identical text in another field.
   Rejected/private values, longer strings, DB admission and source-evidence
   results never enter that memo. It is not serialized; cold and warm process
   costs remain distinct measurements. Field limits, shapes, ownership,
   ordering, capacity and independent metadata/hash checks still run.
   Source-validation material is reused only within its read snapshot; no
   database validator survives it. Unrelated ownership, raw-note and unregistered-control
   inventories are not a new prerequisite for a display projection.
4. Merge changed rows into the checked cache array; recompute ordering/counts,
   build the unchanged public v4 snapshot and render complete HTML. Validate
   all capacities before publishing either file. Protect cache parents,
   targets, links/reparse points, hard links, DB aliases and sibling temporary
   files like the canonical HTML; do not follow or delete an unsafe cache.
5. Flush/close a unique cache temporary and atomically replace the fixed cache
   under a fresh short structural/metadata guard. Its own read transaction
   rechecks physical DB identity, epoch, binding, opt-in, S/I and versions
   against the captured basis and rejects drift. Record the cache tuple C/hash/
   length and clear the entire HTML tuple plus `rendered_generation` atomically
   in a short writer with the same identity/version/invalidation checks;
   S may now be greater than captured S, but C never exceeds S or regresses an
   accepted cache. This invalidates publication even at unchanged S, for a
   projection/format upgrade, same-byte rebuild or presentation-only repair.
   It does not delete the old HTML file or reset its last-success time.
   Hash/readback is outside the DB writer while the artifact
   lock remains held; pin the checked regular file and recheck its identity
   under the writer. This is not a stamp-only content check and no full-file
   hashing occurs while holding the DB writer. No business revision is consumed
   or removed.
6. Publish the prepared HTML with the same kind of fresh guard, checking the
   captured source generation is still current and no newer HTML was accepted.
   The guard's short read transaction covers replacement; ordinary supported
   DB writes cannot commit through it. Record R/hash/length and the existing
   rendered-generation/success fields in one short writer, with identity,
   binding, epoch, I, versions, the exact accepted cache tuple and nonregression
   checks repeated under lock. A complete HTML tuple may exist only for its
   accepted cache; clearing it in step 5 is invalidation, not a publication of
   an older generation. Cache acceptance alone cannot restore R.
   A newer S committed after replacement is allowed and remains due; a changed
   epoch/binding/invalidation or newer accepted publication rejects the result.
7. Recheck once for remaining due work. Churn or failure after the bounded
   opportunity leaves due state and the existing warning. Failure/deferred
   metadata writes use their own structural admission and observed identity/
   generation tuple; stale outcomes cannot overwrite a later success. They
   never perform historical audits or certify C/R.

Structural admission retains existing physical/foreign-key/schema checks; this
change does not turn their cost into a promise of constant total DB work.
Normal capture, guard and outcome must not call the full Evidence Ledger or
global Task-history validators. Full rebuild means rebuilding every display
Task and its consumed relations, not validating unrelated hidden state. Legacy
v5-v33 captures keep their original full admission and never accept this cache.
The retained full-capture reader is independent of cache reuse.

| Interruption or inconsistency | Next eligible action |
|---|---|
| No cache, invalid bytes/hash/header/capacity, incompatible format/projection, DB identity/epoch/binding mismatch, or C < I | Full display rebuild from admitted current DB; do not fall back to stale display bytes as evidence. Preserve last-good HTML if rebuild fails. |
| Crash before cache replacement | Ordinary work retains its previous accepted artifacts/tuples and pending revisions; a version mismatch remains due. Force repair retains the old files but its durably cleared tuples require a full rebuild. |
| Cache replaced, cache metadata commit absent/unknown | File/tuple mismatch requires rebuild; never adopt an orphan file solely from its own header. |
| Accepted cache, HTML replacement fails or process exits | R is null because cache acceptance cleared it, even when C = S equals the former R. Next attempt may reuse matching C and render without re-fetching unchanged evidence. |
| HTML replaced, result metadata commit absent/unknown | Valid new HTML may remain; next opportunity regenerates/replaces under normal guards and records the result. No rollback to older HTML and no inferred success from file presence. |
| New Task change after capture or cache application | Reject a stale replacement before publication; next capture reads revisions above accepted C. If change follows successful HTML replacement, R < S retains due work. |
| Rebind/restore/invalidation between stages | Guards reject old basis, tuples remain due/unusable; next opportunity rebuilds. Never match only numeric project/generation. |
| Cache or HTML externally removed while metadata says current | Cheap presence check makes it due; cache loss rebuilds, HTML-only loss may render from validated cache. No browser-triggered repair. |
| Content changed while still present and metadata current | No immediate audit promise; next reuse/Setup verifies actual bytes. Explicit diagnosis keeps its existing separate DB responsibility. |

### Incremental Execution Boundaries

This bounded three-unit sequence is already registered in lane
`viewer-incremental`; live status/evidence belongs only to the public CLI.
These anchors own its accepted work until all units finish or are explicitly
superseded. Removal/retirement still requires the repository's authorization
and authority-transition rules; completion does not itself delete this plan.

| Unit / Task | Scope and handoff | Gate |
|---|---|---|
| Contract, `tg_task_5237c19d313b8a4c` | This conditional behavior/design, mutation inventory, storage/publication/recovery contracts and acceptance matrix; no runtime or live Setup change. | Document contract check, coupled-owner/code-route consistency and two independent Tier 2 PASS reviews. |
| Storage, `tg_task_38d52d3b2d1c6244` | Ordered schema migration, typed repository, complete transactional notification, indexed range, epoch/invalidation in formal restore/rebind; preserve existing renderer. Depends on Contract. | Temp DB initialization/migration/reentry/preservation/rollback, real mutation-family coverage, concurrent updates, recovery/old-source compatibility, manifest/docs and two independent Tier 2 PASS reviews. |
| Integration, `tg_task_ddfb963bb4aa3912` | Selected Task capture, bounded sanitized cache, guarded publication and metadata, failure recovery, whole-HTML rendering and the performance checks below. Depends on Storage. | Full/delta equality, integration/fault injection/privacy/performance/compatibility, manifest/docs and two independent Tier 2 PASS reviews, then synchronize active owners to enable the new route. |

The integration revision activates the read/publication contract together with
its synchronized owners; storage alone supplies only tracking.
Setup against live state, config/hook changes and external operations retain
their separate permission boundaries. Handoff registration, Evidence
publication and logger optimization are outside these units.

The acceptance suite for Storage/Integration must exercise:

| Case | Required observable result |
|---|---|
| First build, empty project, every mutation family, several updates to one Task, several Tasks | Exact v4 projection/HTML equality to a freshly constructed display snapshot for the same generation/time; no missing Task, duplicate, count/order drift or new field. |
| Current/disabled/no-op/read-only; storage-only installation | No historical SQL/body hashing/cache write in cheap exits; no implicit migration or feature activation; old Viewer still works. |
| Updated-at unchanged; equal timestamps; rollback; mutation failure | Real revisions detect committed changes; rollback/failure leaves neither notification nor phantom display state. |
| Cache loss/corruption, invalid field/privacy/type/size/hash/version and path alias/link | Closed reuse; safe rebuild or fixed failure with good HTML retained; no unsafe file deletion, private-content diagnostic or truncation. |
| Crash at each file/metadata boundary, writer busy, lock contention, new commit between every stage | Matrix above; no future revision removed, older-over-newer publish, false current result or replay of unknown effects. |
| Unchanged S with a projection/format upgrade, same-byte cache rebuild, forced repair or template/reload-policy change | Stop before/after forced invalidation, cache replacement, cache acceptance, HTML replacement and outcome commit. The old HTML must never satisfy current after accepted cache invalidation; force interruption remains due even before cache replacement. Only matching HTML outcome restores current. |
| Same project/S restored twice, DB replacement, migration, confirmed A-B-A rebind | Fresh epoch/identity or binding invalidates the old cache, even if numeric generation repeats; old source rows/evidence stay intact. |
| Supported source v5-v33 and future/invalid source | Original compatibility and explicit migration rules retained; future/corrupt source fails, never masked by cache. |

For performance, create isolated identical 100-Task and 1,000-Task fixtures with
the same changed Task and evidence, then independently increase only unchanged
Tasks' deep review/completion histories. After a full baseline, update one Task
and compare SQL selected IDs/rows, query plans and semantic-validator calls.
Unchanged historical evidence reads/validations must remain zero at both sizes;
changed-task evidence work must not grow with unrelated history. Require index
range plans for revision extraction and selected parent/child queries. Check
Task add/edit/review/completion/checkpoint/reopen paths, not just a private
helper. Full rebuild, full cache-byte verification, full HTML rendering and
structural integrity checks are separate measured work, not disguised as
constant-time operations. Keep existing timing/functional budgets unchanged.
Measure log OFF and ON separately under identical conditions, recording actual
schema/Skill/runtime, SQL counts, lock/read/write lifetimes and inclusive spans;
do not sum nested elapsed times or claim missing sampled SQL was measured.

## Presentation Profile

The only presentation policy is optional:

```text
<physical-skill>/config/viewer.json
```

Absence is valid and disables reload with interval 0. Only the separate explicit
Setup selector may create/toggle it. A present regular physical UTF-8 file is capped at 16,384 bytes and
must contain exactly:

```json
{
  "schema_version": 1,
  "profile": "visibility-refresh-v1",
  "refresh_interval_seconds": 30
}
```

The optional Boolean `enabled` defaults to true and false returns interval zero
without discarding the retained interval. The interval is a JSON integer 5-3,600; booleans, floats,
duplicate/unknown/missing keys, malformed/trailing JSON, links/reparse paths,
devices, directories, replacement races, or uninspectable metadata fail
closed with one sanitized error. The loader uses no-follow where available and
checks descriptor/path identity before and after the bounded read. Doctor does
not inspect this file.

One publication attempt loads the value once and reuses it for both possible
renders. Setup preview treats an invalid present profile as Viewer
repair-required but writes nothing; write setup and routine publication retain
last-good output and use existing incomplete/warning semantics. Missing or
changed profile/template bytes make setup repair the canonical page.

## Browser Application And Reload

The page provides a compact header, all-status summary, search/status/kind/
lane/priority/tag/terminal filters, responsive Task table, detail/events, and
empty state. Terminal Tasks are hidden by default but filterable. Native
controls, visible focus, labels, non-color status cues, bounded radii, and
wrapping support keyboard and narrow-screen use.

Reload scheduling activates only after successful fatal-UTF-8 decode and
render, with a valid nonzero interval and `file:` protocol. One reconciliation
function owns one timeout:

1. clear the prior handle;
2. stop if disabled, already requested, non-file, hidden, search-focused, or
   search IME composition is active;
3. compute elapsed from page-load `performance.now()`;
4. request exactly one same-document reload once elapsed reaches interval; or
5. schedule only the monotonic remainder.

Timeout, visibility-change, search focus/blur, and composition start/end callbacks
reuse that function. A Boolean tracks composition independently of focus, so
either blur/end order stays deferred until both conditions clear. Hidden or
search-active pages own no timer. Focus alone defers indefinitely, without an
idle heuristic; release uses the original epoch, not a restarted interval.
There is no interval timer, polling, wall-clock interval
calculation, fetch/XHR, storage, worker, database access, retry, or message
channel. Browser throttling may make reload late, never early.

Immediately before that automatic reload only, the page may make a one-shot
History API handoff with exact ordered keys:

```text
owner, schema_version, captured_at_ms, search, status, kind, lane, priority, tag,
terminal, selected_task_id, scroll_x, scroll_y, focus_id
```

Owner is `taskgov-viewer-auto-reload`, schema is 2, and compact UTF-8 JSON is at
most 4,096 bytes. Only the exact search input, allowed filter values, optional
visible selected Task ID, nonnegative finite scroll coordinates, and one of
eight fixed control IDs may be saved. Apart from the explicit search input,
Task/snapshot content, arbitrary selectors, URLs/paths, dynamic-row focus,
selection ranges, and nested UI state are prohibited. No
selection is encoded as null and does not prevent capture. Search is a string
bounded by the total envelope, not truncated; oversize or unavailable/failed
storage skips capture without preventing reload.

Capture time is a nonnegative safe integer; status, kind, and priority are
empty or current enums; lane and tag are each at most 1,024 UTF-8 bytes;
selected Task ID is null or nonempty and at most 128 code points; coordinates are
finite integers from 0 through 2,147,483,647; and focus is empty or exactly
`search-filter`, `status-filter`, `kind-filter`, `lane-filter`,
`priority-filter`, `tag-filter`, `terminal-filter`, or `reset-filters`.

Save never overwrites a non-null non-owned state and calls only
`history.replaceState(candidate, "")` without URL. Failure never prevents
reload. On a file load, every owned state is cleared before snapshot decode,
even if malformed, stale, non-reload, or decode-fatal. Restore requires
successful clear, navigation type reload, exact keys/types/bounds,
current owner/version, and age 0-300,000 ms. Old schema-1 envelopes are cleared
but not restored. Apply search and filters before using the existing
`matchesFilters` predicate to restore a still-visible selection. Missing or
filtered selection falls back to normal rendering, never invalidating search
or the other filters. Missing lane/tag options clear only that specific filter.
This also preserves search with no selected Task or zero matches.
Invalid or failed restore returns filters, selection, focus, and scroll to
defaults.

On an enabled file page or one starting with owned state, manual
`history.scrollRestoration` must be set and read back before UI restoration.
If unsupported, state save/restore is disabled but owned state is still
best-effort cleared and reload continues. History state is browser-managed and
may survive session restoration; it is bounded and one-shot, not described as
memory-only.

The page never uses `pushState`, URL/query/fragment state, cookies, Web
Storage, IndexedDB, Cache API, service workers, cross-tab messaging, or manual
reload capture. It logs no state, bytes, validation reason, exception, URL, or
path. Saving/restoring must change neither `history.length` nor
`location.href`.

## Browser Security

The exact CSP is:

```text
default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'none'; img-src 'none'; font-src 'none'; object-src 'none'; media-src 'none'; frame-src 'none'; child-src 'none'; worker-src 'none'; manifest-src 'none'; base-uri 'none'; form-action 'none'
```

Inline script/style is limited to the fixed single-file application;
`unsafe-eval` is absent. Stored values cannot create markup, script, style,
event handlers, URLs, or resource loads. The browser has no external URL,
telemetry, automatic launch, network API, or database-write code.
